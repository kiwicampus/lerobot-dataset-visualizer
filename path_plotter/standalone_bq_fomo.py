"""
Standalone BigQuery client for the `curated_fomo_episodes` table.

This script can be copied to another repository and used independently.
It queries the table: autonomy-286821.data_capture.curated_fomo_episodes

=============================================================================
SETUP INSTRUCTIONS
=============================================================================

1. INSTALL DEPENDENCIES
-----------------------
   pip install google-cloud-bigquery google-auth

   Or add these to your requirements.txt:
       google-cloud-bigquery>=3.2.0
       google-auth>=2.8.0

2. AUTHENTICATION / CREDENTIALS
--------------------------------
   You need Google Cloud credentials with BigQuery read access to the
   project "autonomy-286821" and dataset "data_capture".

   Option A: Application Default Credentials (recommended for local dev)
   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
   Install the gcloud CLI (https://cloud.google.com/sdk/docs/install) and run:

       gcloud auth application-default login --project=autonomy-286821

   This creates a credentials file at:
       ~/.config/gcloud/application_default_credentials.json

   The script will automatically pick this up via google.auth.default().

   Option B: Service Account Key File
   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
   1. Go to GCP Console → IAM & Admin → Service Accounts
   2. Create or use an existing SA with at least these roles:
        - roles/bigquery.dataViewer  (on dataset data_capture)
        - roles/bigquery.jobUser     (on project autonomy-286821)
   3. Download the JSON key file.
   4. Set the env var before running the script:

       export GOOGLE_APPLICATION_CREDENTIALS="/path/to/your/service-account-key.json"

   Option C: Workload Identity / Cloud Run / GCE
   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
   If running on GCP infrastructure (Cloud Run, GKE, Compute Engine),
   attach a service account with the roles above to the resource.
   google.auth.default() will use the metadata server automatically.

3. REQUIRED IAM PERMISSIONS
----------------------------
   The authenticated identity (user or service account) needs:
     - bigquery.jobs.create          → on project autonomy-286821
     - bigquery.tables.getData       → on autonomy-286821.data_capture.curated_fomo_episodes
     - bigquery.tables.get           → on autonomy-286821.data_capture.curated_fomo_episodes

   Simplest approach: grant the predefined roles:
     - roles/bigquery.jobUser   (project-level)
     - roles/bigquery.dataViewer (dataset or table-level)

4. ENVIRONMENT VARIABLES (optional overrides)
----------------------------------------------
   GOOGLE_CLOUD_PROJECT  – override the billing/quota project (default: autonomy-286821)
   GOOGLE_APPLICATION_CREDENTIALS – path to SA key JSON (see Option B)

=============================================================================
"""

from typing import Any, Dict, List, Optional

import google.auth
import google.cloud.bigquery as bigquery

# ── Table Configuration ─────────────────────────────────────────────────────
PROJECT_ID = "autonomy-286821"
DATASET_ID = "data_capture"
TABLE_NAME = "curated_fomo_episodes"
FULL_TABLE_ID = f"`{PROJECT_ID}.{DATASET_ID}.{TABLE_NAME}`"


class BigQueryClient:
    """Standalone BigQuery client for the curated_fomo_episodes table."""

    def __init__(self, project: Optional[str] = None) -> None:
        """Initialize the BigQuery client.

        Args:
            project: GCP project ID to use for billing/quota.
                     If None, it will be inferred from the default credentials.
        """
        credentials, default_project = google.auth.default()
        self.project = project or default_project or PROJECT_ID
        self.client = bigquery.Client(
            project=self.project, credentials=credentials
        )
        print(f"[BigQueryClient] Initialized with project: {self.project}")

    def run_query(self, query: str) -> List[Dict[str, Any]]:
        """Execute a SQL query and return results as a list of dicts.

        Args:
            query: The SQL query string to execute.

        Returns:
            A list of dictionaries, one per row.
        """
        query_job = self.client.query(query)
        results = query_job.result()
        return [dict(row) for row in results]

    def get_all_episodes(
        self, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch episodes from curated_fomo_episodes.

        Args:
            limit: Maximum number of rows to return.

        Returns:
            List of episode records as dictionaries.
        """
        query = f"""
            SELECT *
            FROM {FULL_TABLE_ID}
            ORDER BY timestamp DESC
            LIMIT {limit}
        """
        return self.run_query(query)

    def get_episodes_by_robot(
        self, robot_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch episodes for a specific robot.

        Args:
            robot_id: The robot identifier to filter by.
            limit: Maximum number of rows to return.

        Returns:
            List of episode records as dictionaries.
        """
        query = f"""
            SELECT *
            FROM {FULL_TABLE_ID}
            WHERE robot_id = '{robot_id}'
            ORDER BY timestamp DESC
            LIMIT {limit}
        """
        return self.run_query(query)

    def get_episodes_by_dataset(
        self, dataset_name: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch episodes for a specific dataset name.

        Args:
            dataset_name: The dataset name to filter by.
            limit: Maximum number of rows to return.

        Returns:
            List of episode records as dictionaries.
        """
        query = f"""
            SELECT *
            FROM {FULL_TABLE_ID}
            WHERE dataset_name = '{dataset_name}'
            ORDER BY timestamp DESC
            LIMIT {limit}
        """
        return self.run_query(query)

    def get_episodes_by_fleet(
        self, fleet_name: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch episodes for a specific fleet.

        Args:
            fleet_name: The fleet name to filter by.
            limit: Maximum number of rows to return.

        Returns:
            List of episode records as dictionaries.
        """
        query = f"""
            SELECT *
            FROM {FULL_TABLE_ID}
            WHERE fleet_name = '{fleet_name}'
            ORDER BY timestamp DESC
            LIMIT {limit}
        """
        return self.run_query(query)

    def get_episodes_filtered(
        self,
        robot_id: Optional[str] = None,
        dataset_name: Optional[str] = None,
        fleet_name: Optional[str] = None,
        way_type: Optional[str] = None,
        surface: Optional[str] = None,
        weather: Optional[str] = None,
        time_of_the_day: Optional[str] = None,
        start_timestamp: Optional[str] = None,
        end_timestamp: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Fetch episodes with multiple optional filters.

        Args:
            robot_id: Filter by robot_id.
            dataset_name: Filter by dataset_name.
            fleet_name: Filter by fleet_name.
            way_type: Filter by way_type.
            surface: Filter by surface.
            weather: Filter by weather.
            time_of_the_day: Filter by time_of_the_day.
            start_timestamp: Filter episodes after this timestamp (ISO format).
            end_timestamp: Filter episodes before this timestamp (ISO format).
            limit: Maximum number of rows to return.

        Returns:
            List of episode records as dictionaries.
        """
        query = f"SELECT * FROM {FULL_TABLE_ID}"
        conditions = []

        if robot_id is not None:
            conditions.append(f"robot_id = '{robot_id}'")
        if dataset_name is not None:
            conditions.append(f"dataset_name = '{dataset_name}'")
        if fleet_name is not None:
            conditions.append(f"fleet_name = '{fleet_name}'")
        if way_type is not None:
            conditions.append(f"way_type = '{way_type}'")
        if surface is not None:
            conditions.append(f"surface = '{surface}'")
        if weather is not None:
            conditions.append(f"weather = '{weather}'")
        if time_of_the_day is not None:
            conditions.append(f"time_of_the_day = '{time_of_the_day}'")
        if start_timestamp is not None:
            conditions.append(f"timestamp >= '{start_timestamp}'")
        if end_timestamp is not None:
            conditions.append(f"timestamp <= '{end_timestamp}'")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += f" ORDER BY timestamp DESC LIMIT {limit}"

        return self.run_query(query)

    def get_distinct_values(self, column: str) -> List[Any]:
        """Get all distinct values for a given column.

        Args:
            column: Column name to retrieve distinct values for.

        Returns:
            A list of distinct values.
        """
        query = f"""
            SELECT DISTINCT {column}
            FROM {FULL_TABLE_ID}
            WHERE {column} IS NOT NULL
            ORDER BY {column}
        """
        results = self.run_query(query)
        return [row[column] for row in results]

    def get_distinct_rosbag_names(self, limit: int = 3000) -> List[str]:
        """Distinct non-empty rosbag_name values, sorted (for explorer autocomplete).

        Args:
            limit: Max number of names to return (caller should cap for API safety).

        Returns:
            Rosbag path/id strings as stored in BigQuery.
        """
        cap = max(1, min(int(limit), 10_000))
        query = f"""
            SELECT DISTINCT CAST(rosbag_name AS STRING) AS rosbag_name
            FROM {FULL_TABLE_ID}
            WHERE rosbag_name IS NOT NULL
              AND TRIM(CAST(rosbag_name AS STRING)) != ''
            ORDER BY rosbag_name
            LIMIT {cap}
        """
        results = self.run_query(query)
        return [row["rosbag_name"] for row in results]

    def count_episodes(self) -> int:
        """Get total number of episodes in the table.

        Returns:
            Total row count.
        """
        query = f"SELECT COUNT(*) as total FROM {FULL_TABLE_ID}"
        results = self.run_query(query)
        return results[0]["total"]


# ── Main: Example usage ─────────────────────────────────────────────────────
if __name__ == "__main__":
    client = BigQueryClient()

    # 1. Count total episodes
    total = client.count_episodes()
    print(f"\nTotal episodes in table: {total}")

    # 2. Get the 5 most recent episodes
    print("\n--- 5 most recent episodes ---")
    episodes = client.get_all_episodes(limit=5)
    for ep in episodes:
        print(ep)

    # 3. Show distinct robot IDs
    print("\n--- Distinct robot IDs ---")
    robots = client.get_distinct_values("robot_id")
    for r in robots:
        print(f"  {r}")

    # 4. Show distinct fleet names
    print("\n--- Distinct fleet names ---")
    fleets = client.get_distinct_values("fleet_name")
    for f in fleets:
        print(f"  {f}")

    # 5. Example: filtered query
    # episodes = client.get_episodes_filtered(
    #     fleet_name="my-fleet",
    #     weather="sunny",
    #     start_timestamp="2025-01-01T00:00:00",
    #     limit=10,
    # )

