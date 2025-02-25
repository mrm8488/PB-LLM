from transformers import AutoTokenizer, TextGenerationPipeline
from auto_gptq import AutoGPTQForCausalLM, BaseQuantizeConfig
import logging
from datasets import load_dataset
import random
import os
import torch
from tqdm import tqdm
import numpy as np

def get_wikitext2(nsamples, seed, seqlen, tokenizer):
    cache_file = f"data/wikitext2_enc.pt"
    if not os.path.exists(cache_file):
        traindata = load_dataset("wikitext", "wikitext-2-raw-v1", split="train")
        testdata = load_dataset("wikitext", "wikitext-2-raw-v1", split="test")

        print(f"start tokenize {len(traindata['text'])}")

        trainenc = tokenizer("\n\n".join(traindata["text"][:2000]), return_tensors="pt")
        testenc = tokenizer("\n\n".join(testdata["text"][:2000]), return_tensors="pt")

        torch.save({"train": trainenc, "test": testenc}, cache_file)
    else:
        enc = torch.load(cache_file)
        trainenc = enc["train"]
        testenc = enc["test"]

    random.seed(seed)
    np.random.seed(0)
    torch.random.manual_seed(0)

    traindataset = []
    for _ in tqdm(range(nsamples)):
        i = random.randint(0, trainenc.input_ids.shape[1] - seqlen - 1)
        j = i + seqlen
        inp = trainenc.input_ids[:, i:j]
        attention_mask = torch.ones_like(inp)
        traindataset.append({"input_ids": inp, "attention_mask": attention_mask})
    return traindataset, testenc

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s", level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S"
)


high_percent=0.5
model="facebook/opt-2.7b"
# model="facebook/opt-125m"
pretrained_model_dir = model
quantized_model_dir = f"output/{model}-{high_percent}"
if not os.path.exists(quantized_model_dir):
    os.makedirs(quantized_model_dir)

# tokenizer = AutoTokenizer.from_pretrained(pretrained_model_dir, use_fast=True)
tokenizer = AutoTokenizer.from_pretrained(pretrained_model_dir)
examples = [
    tokenizer(
        "auto-gptq is an easy-to-use model quantization library with user-friendly apis, based on GPTQ algorithm."
    )
]

quantize_config = BaseQuantizeConfig(
    bits=4,  # quantize model to 4-bit
    group_size=128,  # it is recommended to set the value to 128
    desc_act=False,  # set to False can significantly speed up inference but the perplexity may slightly bad
)

low_high_quant_config={
    "high_percent":high_percent,
    "low_bit":1,
    "high_bit":8,
    "binary_method":"xnor",
    "perchannel":True,
    "high_sym":False,
    "high_mse":False
}

traindataset, testenc = get_wikitext2(128, 0, 2048, tokenizer)


# load un-quantized model, by default, the model will always be loaded into CPU memory
model = AutoGPTQForCausalLM.from_pretrained(pretrained_model_dir, quantize_config)
model.quantize(traindataset,low_high_quant_config=low_high_quant_config)

# quantize model, the examples should be list of dict whose keys can only be "input_ids" and "attention_mask"

# model.quantize(examples)

# save quantized model
model.save_quantized(quantized_model_dir)

# save quantized model using safetensors

# push quantized model to Hugging Face Hub.
# to use use_auth_token=True, Login first via huggingface-cli login.
# or pass explcit token with: use_auth_token="hf_xxxxxxx"
# (uncomment the following three lines to enable this feature)
# repo_id = f"YourUserName/{quantized_model_dir}"
# commit_message = f"AutoGPTQ model for {pretrained_model_dir}: {quantize_config.bits}bits, gr{quantize_config.group_size}, desc_act={quantize_config.desc_act}"
# model.push_to_hub(repo_id, commit_message=commit_message, use_auth_token=True)

# alternatively you can save and push at the same time
# (uncomment the following three lines to enable this feature)
# repo_id = f"YourUserName/{quantized_model_dir}"
# commit_message = f"AutoGPTQ model for {pretrained_model_dir}: {quantize_config.bits}bits, gr{quantize_config.group_size}, desc_act={quantize_config.desc_act}"
# model.push_to_hub(repo_id, save_dir=quantized_model_dir, use_safetensors=True, commit_message=commit_message, use_auth_token=True)

# load quantized model to the first GPU
# model = AutoGPTQForCausalLM.from_quantized(quantized_model_dir, device="cuda:0")

# download quantized model from Hugging Face Hub and load to the first GPU
# model = AutoGPTQForCausalLM.from_quantized(repo_id, device="cuda:0", use_safetensors=True, use_triton=False)

# inference with model.generate
print(tokenizer.decode(model.generate(**tokenizer("auto_gptq is", return_tensors="pt").to(model.device))[0]))

# or you can also use pipeline
pipeline = TextGenerationPipeline(model=model, tokenizer=tokenizer)
print(pipeline("auto-gptq is")[0]["generated_text"])

model=model.half()
# test perplexity
from auto_gptq.utils import Perplexity
ppl = Perplexity(model, tokenizer, 'wikitext')
scores=ppl.calculate_perplexity(early_exit=20)
print(f"PPL: {scores}")