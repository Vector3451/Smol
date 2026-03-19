import torch
from transformers import GPT2LMHeadModel, GPT2TokenizerFast

MODEL_PATH = "./nas_model_250m"   # your final SFT model
TOKENIZER_PATH = "./tokenizer/nas_tokenizer_final"

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

def load_model():
    tokenizer = GPT2TokenizerFast.from_pretrained(TOKENIZER_PATH)
    model = GPT2LMHeadModel.from_pretrained(MODEL_PATH)

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model.to(device)
    model.eval()

    return tokenizer, model


def generate(tokenizer, model, prompt):
    inputs = tokenizer(prompt, return_tensors="pt").to(device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=150,
            do_sample=True,
            temperature=0.2,
            top_p=0.85,
            repetition_penalty=1.1,
            no_repeat_ngram_size=0,
            pad_token_id=tokenizer.eos_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )

    return tokenizer.decode(outputs[0], skip_special_tokens=True)


def main():
    tokenizer, model = load_model()

    test_prompts = [
        "### Instruction [SAMBA]:\nCreate a Samba share at /mnt/media for user pi\n\n### Response:\n",
        "### Instruction [SAMBA]:\nList all active Samba connections\n\n### Response:\n",
        "### Instruction [SAMBA]:\nRestart the Samba service completely\n\n### Response:\n",
        
        "### Instruction [RAID]:\nCheck the status of mdadm RAID arrays\n\n### Response:\n",
        "### Instruction [RAID]:\nExpand RAID1 array by adding disk /dev/sdc\n\n### Response:\n",
        
        "### Instruction [NFS]:\nMount an NFS share from 192.168.1.10:/exports/data to /mnt/nfs\n\n### Response:\n",
        "### Instruction [NFS]:\nExport /srv/nas to the local subnet 192.168.1.0/24 via NFS\n\n### Response:\n",
        
        "### Instruction [PERMISSION]:\nChange ownership of /srv/nas/photos to user pi and group users\n\n### Response:\n",
        "### Instruction [PERMISSION]:\nSet permissions of /srv/nas/public to 777\n\n### Response:\n",
        
        "### Instruction [SERVICE]:\nCheck if the ssh service is active\n\n### Response:\n",
        "### Instruction [SERVICE]:\nRestart the ssh service\n\n### Response:\n",
        "### Instruction [SERVICE]:\nReboot the server immediately\n\n### Response:\n"
    ]

    for prompt in test_prompts:
        print("=" * 80)
        print("PROMPT:\n")
        print(prompt)
        print("\nMODEL OUTPUT:\n")
        print(generate(tokenizer, model, prompt))
        print("=" * 80 + "\n")


if __name__ == "__main__":
    main()