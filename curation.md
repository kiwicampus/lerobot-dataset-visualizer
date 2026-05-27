This is a fork of the [Hugging Face Visualizer](https://github.com/huggingface/lerobot-dataset-visualizer/tree/feat/private_repo_viz).

### Dataset Curation Instructions
For our specific dataset curation application, please follow these instructions:

Create a venv and install Next js:

```bash
python3 -m venv .venv
source .venv/bin/activate
npm install
pip install -r ./dataset_curator/requirements.txt
gcloud auth application-default login --project=autonomy-286821
```

Run the curation tool:
```bash 
    python3 dataset_curator/main.py
```

Export HF_TOKEN, REPO_ID and EPISODE_IDS and run visualizer: 

```bash 
   source configs.sh
   npm run dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser and access the dataset repo

