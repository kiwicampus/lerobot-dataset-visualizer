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

Run this 3 terminals: 

1) Run the curation tool:
```bash 
    python3 dataset_curator/main.py
```
2) Run the dataset visualizer:  
```bash 
   source configs.sh
   npm run dev
```

3) Run the path plotter tool: 
```bash 
python3 path_plotter/explorer_app.py 
```

Open [http://localhost:3000](http://localhost:3000) to access the dataset visualizer
Open [http://localhost:5050](http://localhost:5050) to access the path plotter
