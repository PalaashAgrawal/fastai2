# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/37_text.learner.ipynb (unless otherwise specified).

__all__ = ['match_embeds', 'load_ignore_keys', 'clean_raw_keys', 'load_model_text', 'TextLearner', 'decode_spec_tokens',
           'LMLearner', 'language_model_learner', 'text_classifier_learner']

# Cell
from ..basics import *
from .core import *
from .data import *
from .models.core import *
from .models.awdlstm import *
from ..callback.rnn import *
from ..callback.progress import *

# Cell
def match_embeds(old_wgts, old_vocab, new_vocab):
    "Convert the embedding in `old_wgts` to go from `old_vocab` to `new_vocab`."
    bias, wgts = old_wgts.get('1.decoder.bias', None), old_wgts['0.encoder.weight']
    wgts_m = wgts.mean(0)
    new_wgts = wgts.new_zeros((len(new_vocab),wgts.size(1)))
    if bias is not None:
        bias_m = bias.mean(0)
        new_bias = bias.new_zeros((len(new_vocab),))
    old_o2i = old_vocab.o2i if hasattr(old_vocab, 'o2i') else {w:i for i,w in enumerate(old_vocab)}
    for i,w in enumerate(new_vocab):
        idx = old_o2i.get(w, -1)
        new_wgts[i] = wgts[idx] if idx>=0 else wgts_m
        if bias is not None: new_bias[i] = bias[idx] if idx>=0 else bias_m
    old_wgts['0.encoder.weight'] = new_wgts
    if '0.encoder_dp.emb.weight' in old_wgts: old_wgts['0.encoder_dp.emb.weight'] = new_wgts.clone()
    old_wgts['1.decoder.weight'] = new_wgts.clone()
    if bias is not None: old_wgts['1.decoder.bias'] = new_bias
    return old_wgts

# Cell
def _get_text_vocab(dls):
    vocab = dls.vocab
    if isinstance(vocab, L): vocab = vocab[0]
    return vocab

# Cell
def load_ignore_keys(model, wgts):
    "Load `wgts` in `model` ignoring the names of the keys, just taking parameters in order"
    sd = model.state_dict()
    for k1,k2 in zip(sd.keys(), wgts.keys()): sd[k1].data = wgts[k2].data.clone()
    return model.load_state_dict(sd)

# Cell
def _rm_module(n):
    t = n.split('.')
    for i in range(len(t)-1, -1, -1):
        if t[i] == 'module':
            t.pop(i)
            break
    return '.'.join(t)

# Cell
def clean_raw_keys(wgts):
    keys = list(wgts.keys())
    for k in keys:
        t = k.split('.module')
        if f'{_rm_module(k)}_raw' in keys: del wgts[k]
    return wgts

# Cell
def load_model_text(file, model, opt, with_opt=None, device=None, strict=True):
    "Load `model` from `file` along with `opt` (if available, and if `with_opt`)"
    distrib_barrier()
    if isinstance(device, int): device = torch.device('cuda', device)
    elif device is None: device = 'cpu'
    state = torch.load(file, map_location=device)
    hasopt = set(state)=={'model', 'opt'}
    model_state = state['model'] if hasopt else state
    get_model(model).load_state_dict(clean_raw_keys(model_state), strict=strict)
    if hasopt and ifnone(with_opt,True):
        try: opt.load_state_dict(state['opt'])
        except:
            if with_opt: warn("Could not load the optimizer state.")
    elif with_opt: warn("Saved filed doesn't contain an optimizer state.")

# Cell
@log_args(but_as=Learner.__init__)
@delegates(Learner.__init__)
class TextLearner(Learner):
    "Basic class for a `Learner` in NLP."
    def __init__(self, dls, model, alpha=2., beta=1., moms=(0.8,0.7,0.8), **kwargs):
        super().__init__(dls, model, moms=moms, **kwargs)
        self.add_cbs([ModelResetter(), RNNRegularizer(alpha=alpha, beta=beta)])

    def save_encoder(self, file):
        "Save the encoder to `file` in the model directory"
        if rank_distrib(): return # don't save if slave proc
        encoder = get_model(self.model)[0]
        if hasattr(encoder, 'module'): encoder = encoder.module
        torch.save(encoder.state_dict(), join_path_file(file, self.path/self.model_dir, ext='.pth'))

    def load_encoder(self, file, device=None):
        "Load the encoder `file` from the model directory, optionally ensuring it's on `device`"
        encoder = get_model(self.model)[0]
        if device is None: device = self.dls.device
        if hasattr(encoder, 'module'): encoder = encoder.module
        distrib_barrier()
        wgts = torch.load(join_path_file(file,self.path/self.model_dir, ext='.pth'), map_location=device)
        encoder.load_state_dict(clean_raw_keys(wgts))
        self.freeze()
        return self

    def load_pretrained(self, wgts_fname, vocab_fname, model=None):
        "Load a pretrained model and adapt it to the data vocabulary."
        old_vocab = Path(vocab_fname).load()
        new_vocab = _get_text_vocab(self.dls)
        distrib_barrier()
        wgts = torch.load(wgts_fname, map_location = lambda storage,loc: storage)
        if 'model' in wgts: wgts = wgts['model'] #Just in case the pretrained model was saved with an optimizer
        wgts = match_embeds(wgts, old_vocab, new_vocab)
        load_ignore_keys(self.model if model is None else model, clean_raw_keys(wgts))
        self.freeze()
        return self

    #For previous versions compatibility. Remove at release
    @delegates(load_model_text)
    def load(self, file, with_opt=None, device=None, **kwargs):
        if device is None: device = self.dls.device
        if self.opt is None: self.create_opt()
        file = join_path_file(file, self.path/self.model_dir, ext='.pth')
        load_model_text(file, self.model, self.opt, device=device, **kwargs)
        return self

# Cell
def decode_spec_tokens(tokens):
    "Decode the special tokens in `tokens`"
    new_toks,rule,arg = [],None,None
    for t in tokens:
        if t in [TK_MAJ, TK_UP, TK_REP, TK_WREP]: rule = t
        elif rule is None: new_toks.append(t)
        elif rule == TK_MAJ:
            new_toks.append(t[:1].upper() + t[1:].lower())
            rule = None
        elif rule == TK_UP:
            new_toks.append(t.upper())
            rule = None
        elif arg is None:
            try:    arg = int(t)
            except: rule = None
        else:
            if rule == TK_REP: new_toks.append(t * arg)
            else:              new_toks += [t] * arg
    return new_toks

# Cell
@log_args(but_as=TextLearner.__init__)
class LMLearner(TextLearner):
    "Add functionality to `TextLearner` when dealingwith a language model"
    def predict(self, text, n_words=1, no_unk=True, temperature=1., min_p=None, no_bar=False,
                decoder=decode_spec_tokens, only_last_word=False):
        "Return `text` and the `n_words` that come after"
        self.model.reset()
        idxs = idxs_all = self.dls.test_dl([text]).items[0].to(self.dls.device)
        if no_unk: unk_idx = self.dls.vocab.index(UNK)
        for _ in (range(n_words) if no_bar else progress_bar(range(n_words), leave=False)):
            with self.no_bar(): preds,_ = self.get_preds(dl=[(idxs[None],)])
            res = preds[0][-1]
            if no_unk: res[unk_idx] = 0.
            if min_p is not None:
                if (res >= min_p).float().sum() == 0:
                    warn(f"There is no item with probability >= {min_p}, try a lower value.")
                else: res[res < min_p] = 0.
            if temperature != 1.: res.pow_(1 / temperature)
            idx = torch.multinomial(res, 1).item()
            idxs = idxs_all = torch.cat([idxs_all, idxs.new([idx])])
            if only_last_word: idxs = idxs[-1][None]

        num = self.dls.train_ds.numericalize
        tokens = [num.vocab[i] for i in idxs_all if num.vocab[i] not in [BOS, PAD]]
        sep = self.dls.train_ds.tokenizer[-1].sep
        return sep.join(decoder(tokens))

    @delegates(Learner.get_preds)
    def get_preds(self, concat_dim=1, **kwargs): return super().get_preds(concat_dim=1, **kwargs)

# Cell
from .models.core import _model_meta

# Cell
def _get_text_vocab(dls):
    vocab = dls.vocab
    if isinstance(vocab, L): vocab = vocab[0]
    return vocab

# Cell
@log_args(to_return=True, but_as=Learner.__init__)
@delegates(Learner.__init__)
def language_model_learner(dls, arch, config=None, drop_mult=1., pretrained=True, pretrained_fnames=None, **kwargs):
    "Create a `Learner` with a language model from `dls` and `arch`."
    vocab = _get_text_vocab(dls)
    model = get_language_model(arch, len(vocab), config=config, drop_mult=drop_mult)
    meta = _model_meta[arch]
    learn = LMLearner(dls, model, loss_func=CrossEntropyLossFlat(), splitter=meta['split_lm'], **kwargs)
    #TODO: add backard
    #url = 'url_bwd' if data.backwards else 'url'
    if pretrained or pretrained_fnames:
        if pretrained_fnames is not None:
            fnames = [learn.path/learn.model_dir/f'{fn}.{ext}' for fn,ext in zip(pretrained_fnames, ['pth', 'pkl'])]
        else:
            if 'url' not in meta:
                warn("There are no pretrained weights for that architecture yet!")
                return learn
            model_path = untar_data(meta['url'] , c_key='model')
            fnames = [list(model_path.glob(f'*.{ext}'))[0] for ext in ['pth', 'pkl']]
        learn = learn.load_pretrained(*fnames)
    return learn

# Cell
@log_args(to_return=True, but_as=Learner.__init__)
@delegates(Learner.__init__)
def text_classifier_learner(dls, arch, seq_len=72, config=None, pretrained=True, drop_mult=0.5, n_out=None,
                            lin_ftrs=None, ps=None, max_len=72*20, y_range=None, **kwargs):
    "Create a `Learner` with a text classifier from `dls` and `arch`."
    vocab = _get_text_vocab(dls)
    if n_out is None: n_out = get_c(dls)
    assert n_out, "`n_out` is not defined, and could not be infered from data, set `dls.c` or pass `n_out`"
    model = get_text_classifier(arch, len(vocab), n_out, seq_len=seq_len, config=config, y_range=y_range,
                                drop_mult=drop_mult, lin_ftrs=lin_ftrs, ps=ps, max_len=max_len)
    meta = _model_meta[arch]
    learn = TextLearner(dls, model, splitter=meta['split_clas'], **kwargs)
    if pretrained:
        if 'url' not in meta:
            warn("There are no pretrained weights for that architecture yet!")
            return learn
        model_path = untar_data(meta['url'], c_key='model')
        fnames = [list(model_path.glob(f'*.{ext}'))[0] for ext in ['pth', 'pkl']]
        learn = learn.load_pretrained(*fnames, model=learn.model[0])
        learn.freeze()
    return learn

# Cell
@typedispatch
def show_results(x: LMTensorText, y, samples, outs, ctxs=None, max_n=10, **kwargs):
    if ctxs is None: ctxs = get_empty_df(min(len(samples), max_n))
    for i,l in enumerate(['input', 'target']):
        ctxs = [b.show(ctx=c, label=l, **kwargs) for b,c,_ in zip(samples.itemgot(i),ctxs,range(max_n))]
    ctxs = [b.show(ctx=c, label='pred', **kwargs) for b,c,_ in zip(outs.itemgot(0),ctxs,range(max_n))]
    display_df(pd.DataFrame(ctxs))
    return ctxs

# Cell
@typedispatch
def show_results(x: TensorText, y, samples, outs, ctxs=None, max_n=10, trunc_at=150, **kwargs):
    if ctxs is None: ctxs = get_empty_df(min(len(samples), max_n))
    samples = L((s[0].truncate(trunc_at),*s[1:]) for s in samples)
    ctxs = show_results[object](x, y, samples, outs, ctxs=ctxs, max_n=max_n, **kwargs)
    display_df(pd.DataFrame(ctxs))
    return ctxs

# Cell
@typedispatch
def plot_top_losses(x: TensorText, y:TensorCategory, samples, outs, raws, losses, trunc_at=150, **kwargs):
    rows = get_empty_df(len(samples))
    samples = L((s[0].truncate(trunc_at),*s[1:]) for s in samples)
    for i,l in enumerate(['input', 'target']):
        rows = [b.show(ctx=c, label=l, **kwargs) for b,c in zip(samples.itemgot(i),rows)]
    outs = L(o + (TitledFloat(r.max().item()), TitledFloat(l.item())) for o,r,l in zip(outs, raws, losses))
    for i,l in enumerate(['predicted', 'probability', 'loss']):
        rows = [b.show(ctx=c, label=l, **kwargs) for b,c in zip(outs.itemgot(i),rows)]
    display_df(pd.DataFrame(rows))