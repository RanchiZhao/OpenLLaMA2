import os
import datasets as hf_datasets
from transformers import AutoTokenizer

import sys
sys.path.append('/data/OpenLLaMA2')


from openllama2.utils import DeepspeedStrategy

DEFAULT_PAD_TOKEN = "[PAD]"
DEFAULT_EOS_TOKEN = "</s>"
DEFAULT_BOS_TOKEN = "<s>"
DEFAULT_UNK_TOKEN = "<unk>"


def get_tokenizer(pretrain, model, padding_side='left', strategy=None, use_fast=True):
    tokenizer = AutoTokenizer.from_pretrained(pretrain, trust_remote_code=True)
    tokenizer.padding_side = padding_side

    special_tokens_dict = dict()
    if tokenizer.pad_token is None or tokenizer.pad_token_id == tokenizer.eos_token_id:
        special_tokens_dict["pad_token"] = DEFAULT_PAD_TOKEN
        strategy.print('add pad_token')
        tokenizer.add_special_tokens(special_tokens_dict)
    model.resize_token_embeddings(len(tokenizer))

    assert tokenizer.pad_token_id != tokenizer.eos_token_id
    return tokenizer


def get_strategy(args):
    # default args for deepspeed
    if 'seed' not in args:
        args.seed = 42
    if 'max_norm' not in args:
        args.max_norm = 1.0
    if 'micro_train_batch_size' not in args:
        args.micro_train_batch_size = 1
    if 'train_batch_size' not in args:
        args.train_batch_size = 8
    if 'local_rank' not in args:
        args.local_rank = -1
    if 'bf16' not in args:
        args.bf16 = True
    if 'inference_tp_size' not in args:
        args.inference_tp_size = 1
    if 'adam_offload' not in args:
        args.adam_offload = False
    if 'zpg' not in args:
        args.zpg = 8
    # max_out_tokens for DS inference
    if 'max_len' in args and args.max_len is not None:
        args.max_out_tokens = args.max_len
    elif 'generate_max_len' in args and 'prompt_max_len' in args:
        args.max_out_tokens = args.prompt_max_len + args.generate_max_len
    else:
        args.max_out_tokens = 2048

    strategy = DeepspeedStrategy(seed=args.seed, 
                                    max_norm=args.max_norm,
                                    micro_train_batch_size=args.micro_train_batch_size, 
                                    train_batch_size=args.train_batch_size,
                                    zero_stage=args.zero_stage,
                                    max_out_tokens=args.max_out_tokens,
                                    inference_tp_size=args.inference_tp_size,
                                    args=args)

    return strategy


def blending_datasets(datasets, probabilities, strategy=None, seed=42, max_count=2000000, return_eval=True, stopping_strategy="first_exhausted"):
    datasets = datasets.split(',')
    probabilities = list(map(float, probabilities.split(',')))
    assert len(probabilities) == len(datasets)

    train_data_list = []
    eval_data_list = []
    for i, dataset in enumerate(datasets):
        data = hf_datasets.load_dataset('json', data_files=dataset.strip())
        if "train" in data:
            train_data_list.append(data["train"].select(range(min(max_count, len(data["train"])))))
        else:
            train_data_list.append(data.select(range(min(max_count, len(data)))))

        if return_eval:
            if 'test' in data:
                eval_data = data["test"].select(range(min(int(max_count * 0.1), len(data["test"]))))
            elif 'validation' in data:
                eval_data = data["validation"].select(range(min(int(max_count * 0.1), len(data["validation"]))))
            elif "train" in data:
                eval_data = data["train"].select(range(min(int(max_count * 0.1), int(len(data["train"]) * 0.01))))
            else:
                eval_data = data.select(range(min(int(max_count * 0.1), int(len(data) * 0.001))))
            eval_data_list.append(eval_data)
    
    # merge datasets
    if strategy.is_rank_0():
        print(train_data_list)
        
    train_dataset = hf_datasets.interleave_datasets(train_data_list, probabilities=probabilities, seed=seed, stopping_strategy=stopping_strategy)
    if return_eval:
        eval_dataset = hf_datasets.interleave_datasets(eval_data_list, probabilities=probabilities, seed=seed, stopping_strategy=stopping_strategy)
        return train_dataset, eval_dataset
    else:
        return train_dataset


