"""CLI to mark a job as applied. Usage: python mark_applied.py <job_id> [job_id ...]"""
import logging
import sys

import supabase_utils

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def main():
    job_ids = sys.argv[1:]
    if not job_ids:
        print("Usage: python mark_applied.py <job_id> [job_id ...]")
        sys.exit(1)

    for job_id in job_ids:
        supabase_utils.mark_job_applied(job_id)


if __name__ == "__main__":
    main()
