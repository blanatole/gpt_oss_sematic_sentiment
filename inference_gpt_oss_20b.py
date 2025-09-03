import os
import json
from typing import List

import torch
from datasets import load_dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, AutoConfig
from peft import PeftModel


MODEL_ID = os.environ.get("MODEL_ID", "openai/gpt-oss-20b")
ADAPTER_DIR = os.environ.get("ADAPTER_DIR", "gpt-oss-20b-qlora-finetune")
DATA_DIR = os.environ.get("DATA_DIR", os.path.join("jsonl_text"))
TEST_FILE = os.environ.get("TEST_FILE", os.path.join(DATA_DIR, "test.jsonl"))


def load_jsonl_as_list(path: str) -> List[dict]:
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def main():
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Load base model using same approach as training (detect pre-quantization)
    cfg = AutoConfig.from_pretrained(MODEL_ID)
    has_prequant = hasattr(cfg, "quantization_config") and cfg.quantization_config is not None

    if has_prequant:
        # Model already carries a quantization config (e.g., MXFP4). Load as-is.
        base_model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            device_map="auto",
            attn_implementation="eager",
            use_cache=True,
            low_cpu_mem_usage=True,
        )
    else:
        # Fallback to 4-bit bnb quantization
        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        base_model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            device_map="auto",
            attn_implementation="eager",
            use_cache=True,
            quantization_config=quant_config,
            low_cpu_mem_usage=True,
        )
    model = PeftModel.from_pretrained(base_model, ADAPTER_DIR)
    model.eval()

    test_records = load_jsonl_as_list(TEST_FILE)
    to_show = int(os.environ.get("NUM_SAMPLES", 5))
    
    # Debug: check tokenizer EOS token
    print(f"EOS token: '{tokenizer.eos_token}' (ID: {tokenizer.eos_token_id})")
    print(f"PAD token: '{tokenizer.pad_token}' (ID: {tokenizer.pad_token_id})")
    print()
    
    for ex in test_records[:to_show]:
        prompt = ex.get("prompt", "")
        # Don't add EOS to input prompt - let model generate it
        inputs = tokenizer([prompt], return_tensors="pt")
        inputs = {k: v.to(model.device) for k, v in inputs.items()}
        
        with torch.no_grad():
            out = model.generate(
                input_ids=inputs["input_ids"],
                attention_mask=inputs["attention_mask"],
                max_new_tokens=5,  # Chỉ cần tối đa 5 tokens cho " 0" hoặc " 1"
                do_sample=False,
                eos_token_id=tokenizer.eos_token_id,
                pad_token_id=tokenizer.eos_token_id,
                early_stopping=True,
                use_cache=True,
                # Force stop at specific tokens
                stopping_criteria=None,
            )
        
        # Decode only the new tokens (excluding input prompt)
        new_tokens = out[0][inputs["input_ids"].shape[1]:]
        generated_text = tokenizer.decode(new_tokens, skip_special_tokens=True)
        
        # Post-process: chỉ giữ lại phần 0 hoặc 1
        cleaned_output = generated_text.strip()
        
        # Tìm vị trí của " 0" hoặc " 1" trong output
        if " 0" in cleaned_output:
            start_idx = cleaned_output.find(" 0")
            cleaned_output = cleaned_output[start_idx:start_idx+2]  # Lấy " 0"
        elif " 1" in cleaned_output:
            start_idx = cleaned_output.find(" 1")
            cleaned_output = cleaned_output[start_idx:start_idx+2]  # Lấy " 1"
        else:
            # Nếu không có 0 hoặc 1, chỉ lấy ký tự đầu tiên
            cleaned_output = cleaned_output[:2].strip()
        
        print("==== Prompt ====")
        print(prompt)
        print("==== Model output ====")
        print(cleaned_output)
        print()


if __name__ == "__main__":
    main()


