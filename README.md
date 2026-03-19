# Smol NAS AI

![NAS AI](https://img.shields.io/badge/Model-Smol-blue) ![Task](https://img.shields.io/badge/Task-NAS%20Commands-green)

**Smol** is a specialized language model designed to generate administrative commands for Network Attached Storage (NAS) management. It provides a natural language interface for common server tasks across Samba, NFS, RAID, and system permissions.

---

## 🚀 Overview

Smol is a fine-tuned GPT-2 model (approximately 250M parameters) trained to understand instructions related to server administration. Whether you need to expand a RAID array, share a directory via Samba, or check port statuses, Smol generates the corresponding shell commands.

### Key Features
- **Specific Domains**: Supports Samba, NFS, RAID, and general Linux service management.
- **Categorized Prompts**: Uses a structured instruction format to improve accuracy.
- **Efficient Execution**: Includes both standard PyTorch weights and an 8-bit quantized GGUF version for local performance.

---

## 🛠️ Getting Started

### Prerequisites
- **Python 3.8+**
- **Git LFS**: Required to download the large model files.
- **PyTorch & Transformers**: For running the base model.

### Installation

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/Vector3451/Smol.git
   cd Smol
   ```

2. **Initialize Git LFS**:
   Ensure large files are downloaded correctly:
   ```bash
   git lfs pull
   ```

3. **Install Dependencies**:
   ```bash
   pip install torch transformers
   ```

---

## 💻 Usage

### 1. PyTorch Inference (transformers)
Use the included `test.py` script to run inference on a variety of pre-defined NAS prompts:
```bash
python test.py
```
This script loads the model from `./nas_model_250m` and the tokenizer from `./tokenizer`.

### 2. GGUF Local Execution
For low-resource environments, use the quantized model:
```bash
./llama_cpp_repo/build/bin/llama-cli -m nas_model_q8.gguf -p "### Instruction [SAMBA]:\nCreate a share at /mnt/data\n\n### Response:\n"
```

### 3. Environment Diagnostics
Use `NAS/diagnostic.py` to check if your current system is ready for NAS operations:
```bash
python NAS/diagnostic.py
```

---

## 📂 Project Structure

- `nas_model_250m/`: Full PyTorch model weights.
- `nas_model_q8.gguf`: 8-bit quantized model for fast CPU inference.
- `tokenizer/`: Vocabulary and merge files for the custom tokenizer.
- `NAS/`: Utility scripts and diagnostic tools.
- `test.py`: Primary testing and demonstration script.
- `convert_to_gguf.py`: Script to generate new GGUF versions if needed.

---

## 🧠 How it Works

Smol is built on the `GPT2LMHeadModel` architecture. It has been fine-tuned using a custom dataset of NAS administration scenarios. The model expects input in the following format:

```text
### Instruction [CATEGORY]:
[User Request]

### Response:
```

Supported categories include `[SAMBA]`, `[RAID]`, `[NFS]`, `[PERMISSION]`, and `[SERVICE]`.

---

## ⚖️ License
This project is for educational and research purposes. Please verify any generated commands before running them on a production server.
