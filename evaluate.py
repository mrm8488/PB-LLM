import torch
import torch.nn as nn
from tqdm import tqdm
import os
from datautils import get_loaders
from lm_eval.base import BaseLM
from lm_eval import evaluator
import time


class EvalLM(BaseLM):
    def __init__(
        self,
        model,
        tokenizer,
        device="cuda",
        batch_size=1,
    ):
        super().__init__()

        assert isinstance(device, str)
        assert isinstance(batch_size, int)

        self._device = torch.device(device)

        self.model = model.to(self.device)
        self.model.eval()

        self.tokenizer = tokenizer

        self.vocab_size = self.tokenizer.vocab_size

        self.batch_size_per_gpu = batch_size  # todo: adaptive batch size

    @property
    def eot_token_id(self):
        # we use EOT because end of *text* is more accurate for what we're doing than end of *sentence*
        return self.tokenizer.eos_token_id

    @property
    def max_length(self):
        try:
            return self.model.config.n_ctx
        except AttributeError:
            # gptneoconfig doesn't have n_ctx apparently
            return self.model.config.max_position_embeddings

    @property
    def max_gen_toks(self):
        return 256

    @property
    def batch_size(self):
        # TODO: fix multi-gpu
        return self.batch_size_per_gpu  # * gpus

    @property
    def device(self):
        # TODO: fix multi-gpu
        return self._device

    def tok_encode(self, string: str):
        return self.tokenizer.encode(string, add_special_tokens=False)

    def tok_decode(self, tokens):
        return self.tokenizer.decode(tokens)

    def _model_call(self, inps):
        """
        inps: a torch tensor of shape [batch, sequence]
        the size of sequence may vary from call to call

        returns: a torch tensor of shape [batch, sequence, vocab] with the
        logits returned from the model
        """
        with torch.no_grad():
            return self.model(inps)[0][:, :, :50257]

    def _model_generate(self, context, max_length, eos_token_id):
        return self.model.generate(
            context, max_length=max_length, eos_token_id=eos_token_id, do_sample=False
        )


@torch.no_grad()
def evaluate_model(
    model,
    tokenizer,
    model_name,
    tasks,
    eval_ppl=False,
    seed=1,
    num_fewshot=0,
    cache_dir="./data/",
    limit=-1,
    batch_size=1,
):
    """
    model: model name
    limit: number of test samples for debug, set to -1 is no limit
    tasks: str tasks are split by ,
    num_fewshot: Number of examples in few-shot context
    """
    lm = EvalLM(model, tokenizer, batch_size=batch_size)
    results = {}
    if eval_ppl:
        for dataset in ["wikitext2", "ptb", "c4"]:
            # for dataset in ['c4']:
            if "opt" in model_name:
                cache_testloader = f"/tmp/{dataset}_testloader_opt_all.cache"
                if os.path.exists(cache_testloader):
                    testloader = torch.load(cache_testloader)
                    # print(f"load calibration from {cache_testloader}")
                else:
                    dataloader, testloader = get_loaders(
                        dataset,
                        seed=seed,
                        model=model_name,
                        seqlen=lm.seqlen,
                        cache_dir=cache_dir,
                    )
                    torch.save(testloader, cache_testloader)
            elif "llama" in model_name:
                cache_testloader = f"/tmp/{dataset}_testloader_llama_all.cache"
                if os.path.exists(cache_testloader):
                    testloader = torch.load(cache_testloader)
                    # print(f"load calibration from {cache_testloader}")
                else:
                    dataloader, testloader = get_loaders(
                        dataset,
                        seed=seed,
                        model=model_name,
                        seqlen=lm.seqlen,
                        cache_dir=cache_dir,
                    )
                    torch.save(testloader, cache_testloader)
            # print(dataset)
            if "c4" == dataset:
                testenc = testloader
            else:
                testenc = (
                    testloader.input_ids
                )  # 有个小坑 如果某个input_ids 有超过2*2048个词，nsamples 就不准了

            nsamples = testenc.numel() // lm.seqlen
            use_cache = lm.model.config.use_cache
            lm.model.config.use_cache = False
            lm.model.eval()
            nlls = []

            for i in tqdm(range(nsamples)):
                batch = testenc[:, (i * lm.seqlen) : ((i + 1) * lm.seqlen)].to(
                    lm.device
                )
                if "opt" in model_name:
                    outputs = lm.model.model.decoder(batch)
                elif "llama" in model_name:
                    outputs = lm.model.model(batch)
                hidden_states = outputs[0]  # .to(lm.model.lm_head.weight.device)
                logits = lm.model.lm_head(hidden_states)  # .contiguous()
                shift_logits = logits[:, :-1, :]  # .contiguous()
                shift_labels = testenc[:, (i * lm.seqlen) : ((i + 1) * lm.seqlen)][
                    :, 1:
                ].to(lm.model.lm_head.weight.device)
                loss_fct = nn.CrossEntropyLoss()
                loss = loss_fct(
                    shift_logits.view(-1, shift_logits.size(-1)),
                    shift_labels.view(-1),
                )
                neg_log_likelihood = loss.float() * lm.seqlen
                nlls.append(neg_log_likelihood)
                if i == limit:
                    break
                if i == 1:
                    print(
                        "memory_allocated",
                        i,
                        torch.cuda.memory_allocated() / 1024 / 1024,
                        "max memory_allocated",
                        torch.cuda.max_memory_allocated() / 1024**2,
                    )

            ppl = torch.exp(torch.stack(nlls).sum() / (nsamples * lm.seqlen))
            print(dataset, ppl.item())
            lm.model.config.use_cache = use_cache
            # pprint(model)
            results[dataset] = ppl.item()
    if tasks == "mmlu":
        tasks = "hendrycksTest-abstract_algebra,hendrycksTest-anatomy,hendrycksTest-astronomy,hendrycksTest-business_ethics,hendrycksTest-clinical_knowledge,hendrycksTest-college_biology,hendrycksTest-college_chemistry,hendrycksTest-college_computer_science,hendrycksTest-college_mathematics,hendrycksTest-college_medicine,hendrycksTest-college_physics,hendrycksTest-computer_security,hendrycksTest-conceptual_physics,hendrycksTest-econometrics,hendrycksTest-electrical_engineering,hendrycksTest-elementary_mathematics,hendrycksTest-formal_logic,hendrycksTest-global_facts,hendrycksTest-high_school_biology,hendrycksTest-high_school_chemistry,hendrycksTest-high_school_computer_science,hendrycksTest-high_school_european_history,hendrycksTest-high_school_geography,hendrycksTest-high_school_government_and_politics,hendrycksTest-high_school_macroeconomics,hendrycksTest-high_school_mathematics,hendrycksTest-high_school_microeconomics,hendrycksTest-high_school_physics,hendrycksTest-high_school_psychology,hendrycksTest-high_school_statistics,hendrycksTest-high_school_us_history,hendrycksTest-high_school_world_history,hendrycksTest-human_aging,hendrycksTest-human_sexuality,hendrycksTest-international_law,hendrycksTest-jurisprudence,hendrycksTest-logical_fallacies,hendrycksTest-machine_learning,hendrycksTest-management,hendrycksTest-marketing,hendrycksTest-medical_genetics,hendrycksTest-miscellaneous,hendrycksTest-moral_disputes,hendrycksTest-moral_scenarios,hendrycksTest-nutrition,hendrycksTest-philosophy,hendrycksTest-prehistory,hendrycksTest-professional_accounting,hendrycksTest-professional_law,hendrycksTest-professional_medicine,hendrycksTest-professional_psychology,hendrycksTest-public_relations,hendrycksTest-security_studies,hendrycksTest-sociology,hendrycksTest-us_foreign_policy,hendrycksTest-virology,hendrycksTest-world_religions"
    if tasks == "llmqat":
        tasks = 'boolq,piqa,hellaswag,winogrande,arc_easy,arc_challenge,openbookqa'
    if tasks != "":
        t_results = evaluator.simple_evaluate(
            lm,
            tasks=tasks.split(","),
            batch_size=batch_size,
            num_fewshot=num_fewshot,
            limit=None if limit == -1 else limit,
            no_cache=True,
        )
        results.update(t_results)
        print(results)
    return results
