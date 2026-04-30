This is a fork of the [Hugging Face Visualizer](https://github.com/huggingface/lerobot-dataset-visualizer/tree/feat/private_repo_viz).

### Dataset Curation Instructions
For our specific dataset curation application, please follow these instructions:

Create a venv and install Next js:

```bash
python -m venv .venv
source .venv/bin/activate
npm install
npm audit fix ( in case errors appear in the install)
npm install next@15.3.6 ( in case errors appear in the install)
pip install -r dataset_curator/requirements.txt
```

Export HF_TOKEN and start the visualizer: 

```bash 
   export HF_TOKEN=<token>
   nmp run dev
```

In another terminal run:
```bash 
    python3 dataset_curator/main.py
```

Open [http://localhost:3000](http://localhost:3000) with your browser and access the dataset repo

