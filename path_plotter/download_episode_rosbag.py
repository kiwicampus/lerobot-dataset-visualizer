import argparse
import csv
import os
import time
import traceback
from pathlib import Path

#from b2sdk.v2 import B2Api, InMemoryAccountInfo

DEFAULT_CSV = Path("/home/yaisa/lerobot-dataset-visualizer/dataset_20260523_splits.csv")


def find_rosbag(episode_index: int, csv_path: Path) -> str:
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if int(row["dataset_episode_index"]) == episode_index:
                return row["rosbag_name"]
    raise ValueError(f"Episode index {episode_index} not found in {csv_path}")


# def download_files_from_b2(rosbag_name: str):
#     try:
#         rosbag_name = os.path.basename(str(rosbag_name).rstrip("/"))

#         api = B2Api(InMemoryAccountInfo())
#         api.authorize_account("production", os.environ["B2_KEY_ID"], os.environ["B2_APPLICATION_KEY"])
#         bucket = api.get_bucket_by_name(os.environ["BUCKET_NAME"])

#         prefix = f"rosbags/fomo/{rosbag_name}/"
#         local_dir = os.path.join("rosbags", rosbag_name)
#         os.makedirs(local_dir, exist_ok=True)

#         print(f"Descargando a: {local_dir}")
#         print(f"Origen: b2://{os.environ['BUCKET_NAME']}/{prefix}")

#         mcap_files = [
#             fv for fv, _ in bucket.ls(folder_to_list=prefix, recursive=True)
#             if fv.file_name.endswith(".mcap")
#         ]

#         start_time = time.time()
#         for fv in mcap_files:
#             local_path = os.path.join(local_dir, os.path.basename(fv.file_name))
#             if os.path.exists(local_path):
#                 print(f"   -> Ya existe, saltando: {os.path.basename(fv.file_name)}")
#                 continue
#             print(f"   -> Descargando: {os.path.basename(fv.file_name)}")
#             bucket.download_file_by_name(fv.file_name).save_to(local_path)

#         elapsed = time.time() - start_time
#         print(f"Listo en {elapsed:.1f}s")

#     except Exception as e:
#         print(f"Error al descargar de B2: {e}")
#         traceback.print_exc()

