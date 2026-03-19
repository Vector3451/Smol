from tokenizers import ByteLevelBPETokenizer
import os

# ===== CONFIG =====

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CORPUS_FILE = os.path.join(BASE_DIR, "../data/cleaned/stackexchange_nas.txt")
OUTPUT_DIR = os.path.join(BASE_DIR, "nas_tokenizer_final")

VOCAB_SIZE = 32000  # Use 32000 for mixed English + commands

SPECIAL_TOKENS = [
    "<s>",
    "<pad>",
    "</s>",
    "<unk>",
    "<mask>",
]

# ===================


def main():
    print("Training tokenizer on corpus...")
    
    tokenizer = ByteLevelBPETokenizer()

    tokenizer.train(
        files=[CORPUS_FILE],
        vocab_size=VOCAB_SIZE,
        min_frequency=2,
        special_tokens=SPECIAL_TOKENS,
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    tokenizer.save_model(OUTPUT_DIR)

    print("Tokenizer training complete.")
    print(f"Saved to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()