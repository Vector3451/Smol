from transformers import GPT2TokenizerFast
import os

TOKENIZER_DIR = "./tokenizer/nas_tokenizer_final"
OUTPUT_DIR = "./tokenizer/nas_tokenizer_hf"

def main():
    tokenizer = GPT2TokenizerFast(
        vocab_file=f"{TOKENIZER_DIR}/vocab.json",
        merges_file=f"{TOKENIZER_DIR}/merges.txt",
        unk_token="<unk>",
        bos_token="<s>",
        eos_token="</s>",
        pad_token="<pad>",
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    tokenizer.save_pretrained(OUTPUT_DIR)

    print("HF tokenizer saved.")

if __name__ == "__main__":
    main()
