import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import time 
import random 
import logging
import config
from sources import user_agents
from db import supabase_utils
from sources import scrape_guard
from sources import dedup
from markdownify import markdownify as md
import re
import sys
import argparse

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Internship / student-role filter ---
# Word-boundary patterns so "intern" does NOT match "international"/"internal".
# German prefixes (werkstudent, praktikum, ...) are left open-ended to catch compounds.
_INTERNSHIP_RE = re.compile(
    r"\bintern(ship)?s?\b"
    r"|\bworking student\b"
    r"|\bstudent (employee|assistant)\b"
    r"|\bwerkstudent"
    r"|\bpraktik(um|ant)"
    r"|\bthesis\b"
    r"|\babschlussarbeit"
    r"|\bmasterarbeit"
    r"|\bbachelorarbeit"
    r"|\bfinal (year )?project\b",
    re.IGNORECASE,
)

def is_internship_role(title: str | None, level: str | None = None) -> bool:
    """True if the job title or seniority level indicates an internship/student/thesis role."""
    return bool(_INTERNSHIP_RE.search(title or "")) or bool(_INTERNSHIP_RE.search(level or ""))

# --- Freelance / contract role filter ---
# We want permanent employment (or working-student) roles only, not gig/contract work.
_FREELANCE_RE = re.compile(
    r"\bfreelance"
    r"|\bfreelancer"
    r"|\bcontractor\b"
    r"|\(contract\)"
    r"|\bcontract[- ]based\b"
    r"|\bself[- ]employed\b"
    r"|\bselbst(st)?(ä|ae)ndig"
    r"|\bfreiberufl"
    r"|\bauftragnehmer",
    re.IGNORECASE,
)

def is_freelance_role(title: str | None, level: str | None = None) -> bool:
    """True if the job title or seniority level indicates a freelance/contractor/self-employed role."""
    return bool(_FREELANCE_RE.search(title or "")) or bool(_FREELANCE_RE.search(level or ""))

_RELATIVE_TIME_RE = re.compile(
    r"(\d+)\+?\s*(minute|hour|day|week|month|minuten?|stunden?|tag(?:en)?|wochen?|monat(?:en)?)",
    re.IGNORECASE,
)
_TIME_UNIT_DAYS = {
    "minute": 0, "minuten": 0, "stunde": 0, "stunden": 0, "hour": 0,
    "day": 1, "tag": 1, "tagen": 1,
    "week": 7, "woche": 7, "wochen": 7,
    "month": 30, "monat": 30, "monaten": 30,
}

def parse_relative_posted_time(text: str | None) -> str | None:
    """
    Converts LinkedIn's relative posting time ('3 days ago', 'vor 2 Wochen', '30+ days ago')
    to an absolute ISO-8601 UTC timestamp. Returns None if it can't be parsed.
    """
    if not text:
        return None
    match = _RELATIVE_TIME_RE.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower().rstrip("s")
    days_per_unit = _TIME_UNIT_DAYS.get(unit, _TIME_UNIT_DAYS.get(unit + "s"))
    if days_per_unit is None:
        return None
    from datetime import timezone
    if days_per_unit == 0:
        # minutes/hours ago → posted today
        posted = datetime.now(timezone.utc)
    else:
        posted = datetime.now(timezone.utc) - timedelta(days=amount * days_per_unit)
    return posted.isoformat()

# Convert HTML description to Markdown
def convert_html_to_markdown(html: str) -> str | None:
    """
    Convert HTML to clean Markdown using BeautifulSoup (to strip unwanted tags)
    and markdownify (to convert the cleaned HTML to Markdown).
    No LLM API calls are made — this is entirely local.
    """
    if not html or not html.strip():
        logging.info("Received empty HTML for Markdown conversion, returning empty string.")
        return ""

    try:
        # Clean the HTML: remove scripts, styles, nav, and other non-content tags
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'iframe', 'noscript']):
            tag.decompose()

        cleaned_html = str(soup)

        # Convert cleaned HTML to Markdown
        markdown_text = md(
            cleaned_html,
            heading_style="ATX",
            bullets="-",
            strip=['img'],
        )

        # Clean up excessive blank lines
        lines = markdown_text.splitlines()
        cleaned_lines = []
        prev_blank = False
        for line in lines:
            if not line.strip():
                if not prev_blank:
                    cleaned_lines.append('')
                prev_blank = True
            else:
                cleaned_lines.append(line)
                prev_blank = False
        markdown_text = '\n'.join(cleaned_lines).strip()

        logging.info("Successfully converted HTML to Markdown.")
        return markdown_text if markdown_text else ""
    except Exception as e:
        logging.error(f"Error during HTML to Markdown conversion: {e}")
        return None

# --- LinkedIn Scraping Logic ---
def _fetch_linkedin_job_ids(search_query: str, location: str,
                            outcome: "scrape_guard.SourceOutcome | None" = None) -> list:
    """Fetches job IDs from LinkedIn search results pages with delays, rotating user agents, and retries.

    Every search response is recorded on `outcome` with its HTTP status and body
    size, so that a run yielding zero jobs can be read as a block rather than as
    an empty result set. See scrape_guard.
    """

    def _record(status_code=None, body_bytes=0, items_parsed=0, error=""):
        if outcome is not None:
            outcome.record_attempt(search_query, status_code=status_code,
                                   body_bytes=body_bytes, items_parsed=items_parsed,
                                   error=error)

    job_ids_list = []
    start = 0
    max_start = config.LINKEDIN_MAX_START


    logging.info(f"--- Starting Phase 1: Scraping Job IDs (Max Start: {max_start}) ---")
    while start <= max_start:
        target_url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={search_query.replace(' ', '%20')}&location={location}&geoId={config.LINKEDIN_GEO_ID}&f_TPR={config.LINKEDIN_JOB_POSTING_DATE}&f_JT={config.LINKEDIN_JOB_TYPE}&f_WT={config.LINKEDIN_F_WT}&f_E={config.LINKEDIN_F_E}&start={start}"

        if start > 0:
            sleep_time = random.uniform(5.0, 15.0)
            logging.info(f"Waiting for {sleep_time:.2f} seconds before next request...")
            time.sleep(sleep_time)

        user_agent = random.choice(user_agents.USER_AGENTS)
        headers = {'User-Agent': user_agent}
    
        logging.info(f"Using User-Agent: {user_agent}")

    
        logging.info(f"Scraping URL: {target_url}")

        res = None 
        retries = 0
        while retries <= config.MAX_RETRIES:
            try:
                res = requests.get(target_url, headers=headers, timeout=config.REQUEST_TIMEOUT)
                res.raise_for_status()
                break
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429 and retries < config.MAX_RETRIES:
                    retries += 1
                    wait_time = config.RETRY_DELAY_SECONDS + random.uniform(0, 5) 
                    
                    logging.warning(f"Error 429: Too Many Requests. Retrying attempt {retries}/{config.MAX_RETRIES} after {wait_time:.2f} seconds...")
                    time.sleep(wait_time)

                    user_agent = random.choice(user_agents.USER_AGENTS)
                    headers = {'User-Agent': user_agent}
                
                    logging.info(f"Retrying with new User-Agent: {user_agent}")
                    continue
                else:
                    
                    logging.error(f"HTTP Error fetching search results page: {e}")
                    body = e.response.text if e.response is not None else ""
                    _record(status_code=e.response.status_code if e.response is not None else None,
                            body_bytes=len(body), error=f"HTTPError {e}"[:120])
                    res = None 
                    break
            except requests.exceptions.RequestException as e:
                
                logging.error(f"Request Exception fetching search results page: {e}")
                _record(error=f"RequestException {e}"[:120])
                res = None
                break

        
        if res is None:
            logging.error(f"Failed to fetch {target_url} after {retries} retries. Stopping pagination for this query.")
            break 

        if not res.text:
            
             logging.info(f"Received empty response text at start={start}, stopping.")
             _record(status_code=res.status_code, body_bytes=0, items_parsed=0)
             break

        soup = BeautifulSoup(res.text, 'html.parser')
        all_jobs_on_this_page = soup.find_all('li')

        if not all_jobs_on_this_page:
            
             logging.info(f"No job listings ('li' elements) found on page at start={start}, stopping.")
             _record(status_code=res.status_code, body_bytes=len(res.text), items_parsed=0)
             break

    
        logging.info(f"Found {len(all_jobs_on_this_page)} potential job elements on this page.")

        jobs_found_this_iteration = 0
        for job_element in all_jobs_on_this_page:
            base_card = job_element.find("div", {"class": "base-card"})
            job_urn = base_card.get('data-entity-urn') if base_card else None
            if job_urn and 'jobPosting:' in job_urn:
                try:
                    jobid = job_urn.split(":")[3]
                    if jobid not in job_ids_list:
                         job_ids_list.append(jobid)
                         jobs_found_this_iteration += 1
                except IndexError:
                    
                    logging.warning(f"Could not parse job ID from URN: {job_urn}")
                    pass

    
        logging.info(f"Added {jobs_found_this_iteration} unique job IDs from this page.")
        _record(status_code=res.status_code, body_bytes=len(res.text),
                items_parsed=jobs_found_this_iteration)

        if jobs_found_this_iteration == 0 and len(all_jobs_on_this_page) > 0:
        
            logging.info("Found list items but no new job IDs extracted, potentially end of relevant results or parsing issue.")
            break

        start += 10


    logging.info(f"--- Finished Phase 1: Found {len(job_ids_list)} unique job IDs during scraping ---")
    return job_ids_list

def _fetch_linkedin_job_details(job_id: str) -> dict | None:
    """Fetches detailed information for a single job ID with delays, rotating user agents, and retries."""

    job_detail_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

    logging.info(f"Preparing to fetch details for job ID: {job_id}")

    sleep_time = random.uniform(3.0, 10.0)

    logging.info(f"Waiting for {sleep_time:.2f} seconds before fetching details...")
    time.sleep(sleep_time)

    user_agent = random.choice(user_agents.USER_AGENTS)
    headers = {'User-Agent': user_agent}

    logging.info(f"Using User-Agent for details: {user_agent}")


    logging.info(f"Fetching details from: {job_detail_url}")

    resp = None 
    retries = 0
    while retries <= config.MAX_RETRIES:
        try:
            resp = requests.get(job_detail_url, headers=headers, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429 and retries < config.MAX_RETRIES:
                retries += 1
                wait_time = config.RETRY_DELAY_SECONDS + random.uniform(0, 5) 
                
                logging.warning(f"Error 429 for job ID {job_id}. Retrying attempt {retries}/{config.MAX_RETRIES} after {wait_time:.2f} seconds...")
                time.sleep(wait_time)
                user_agent = random.choice(user_agents.USER_AGENTS)
                headers = {'User-Agent': user_agent}
            
                logging.info(f"Retrying job {job_id} with new User-Agent: {user_agent}")
                continue
            else:
                
                logging.error(f"HTTP Error fetching details for job ID {job_id}: {e}")
                return None
        except requests.exceptions.RequestException as e:
            
            logging.error(f"Request Exception fetching details for job ID {job_id}: {e}")
            return None 

    
    if resp is None:
         logging.error(f"Failed to fetch details for job ID {job_id} after {retries} retries (unexpected state).")
         return None

    try:
        soup = BeautifulSoup(resp.text, 'html.parser')
        job_details = {"job_id": job_id}

        # --- Extract Company ---
        try:
            company_img = soup.find("div",{"class":"top-card-layout__card"}).find("a").find("img")
            if company_img:
                job_details["company"] = company_img.get('alt').strip()
            if not job_details.get("company"):
                 company_link = soup.find("a", {"class": "topcard__org-name-link"})
                 if company_link:
                      job_details["company"] = company_link.text.strip()
                 else:
                      sub_title_span = soup.find("span", {"class": "topcard__flavor"})
                      if sub_title_span:
                           job_details["company"] = sub_title_span.text.strip()

            if not job_details.get("company"):
                 job_details["company"] = None
                 logging.warning(f"Could not extract company for job ID {job_id}")
        except Exception as e:
            logging.error(f"Error extracting company for job ID {job_id}: {e}")
            job_details["company"] = None

        # --- Extract Job Title ---
        try:
            title_link = soup.find("div",{"class":"top-card-layout__entity-info"}).find("a")
            job_details["job_title"] = title_link.text.strip() if title_link else None
            if not job_details["job_title"]:
                 title_h1 = soup.find("h1", {"class": "top-card-layout__title"})
                 if title_h1:
                      job_details["job_title"] = title_h1.text.strip()
        except Exception as e: 
            logging.error(f"Error extracting job title for job ID {job_id}: {e}")
            job_details["job_title"] = None

        # --- Extract Seniority Level ---
        try:
            # Find all criteria items
            criteria_items = soup.find("ul",{"class":"description__job-criteria-list"}).find_all("li")
            job_details["level"] = None 
            for item in criteria_items:
                header = item.find("h3", {"class": "description__job-criteria-subheader"})
                if header and "Seniority level" in header.text:
                    level_text = item.find("span", {"class": "description__job-criteria-text"})
                    if level_text:
                        job_details["level"] = level_text.text.strip()
                        break 
        except Exception as e: 
            logging.error(f"Error extracting seniority level for job ID {job_id}: {e}")
            job_details["level"] = None

        # --- Extract Location ---
        try:
           
            location_span = soup.find("span", {"class": "topcard__flavor topcard__flavor--bullet"})
            if location_span:
                job_details["location"] = location_span.text.strip()
            else:
                
                subtitle_div = soup.find("div", {"class": "topcard__flavor-row"})
                if subtitle_div:
                    location_span_fallback = subtitle_div.find("span", {"class": "topcard__flavor"})
                    if location_span_fallback:
                         job_details["location"] = location_span_fallback.text.strip()

            if not job_details.get("location"): 
                 job_details["location"] = None
                 logging.warning(f"Could not extract location for job ID {job_id}")
        except Exception as e:
            logging.error(f"Error extracting location for job ID {job_id}: {e}")
            job_details["location"] = None

        # --- Extract Description ---
        description_html = "" 
        try:
            description_div = soup.find("div", {"class": "show-more-less-html__markup"})
            if description_div:
                description_html = str(description_div)
            else:
                logging.warning(f"Could not find description div for job ID {job_id}")
        except Exception as e:
                logging.error(f"Error extracting description HTML for job ID {job_id}: {e}")
                description_html = ""

        if description_html.strip():
            job_details["description"] = convert_html_to_markdown(description_html)
        else:
            job_details["description"] = None 
            logging.warning(f"Description HTML was empty for job ID {job_id}. Skipping conversion.") 

        # --- Extract Posted Date (relative text like '3 days ago' / 'vor 2 Wochen') ---
        try:
            time_span = soup.find("span", {"class": "posted-time-ago__text"})
            posted_at = parse_relative_posted_time(time_span.text.strip() if time_span else None)
            if posted_at:
                job_details["posted_at"] = posted_at
        except Exception as e:
            logging.debug(f"Could not extract posted date for job ID {job_id}: {e}")

        # --- Set Provider & URL ---
        job_details["provider"] = "linkedin"
        job_details["job_url"] = f"https://www.linkedin.com/jobs/view/{job_id}/"

        return job_details

    except Exception as e:
         
         logging.error(f"General Error processing details for job ID {job_id} after successful fetch: {e}")
         return None

def process_linkedin_query(search_query: str, location: str, limit: int = None,
                           outcome: "scrape_guard.SourceOutcome | None" = None) -> list:
    """
    Orchestrates scraping and detail fetching for a single query,
    filtering against existing jobs in Supabase BEFORE fetching details.
    Returns a list of new job details found.

    `outcome` accumulates how many postings the search returned (before dedup)
    against how many were new, so the run can tell a broken source from a quiet one.
    """

    scraped_job_ids = _fetch_linkedin_job_ids(search_query, location, outcome=outcome)
    if not scraped_job_ids:
    
        logging.info("No job IDs found in Phase 1. Skipping detail fetching.")
        return []

    unique_linkedin_job_ids = list(set(scraped_job_ids))

    def _filtered(reason):
        if outcome is not None:
            outcome.record_filtered(reason)

    logging.info(f"Found {len(scraped_job_ids)} raw job IDs, {len(unique_linkedin_job_ids)} unique IDs after scraping.")


    logging.info("\n--- Starting Filtering Step: Checking against Supabase ---")
    job_ids_set, company_title_set = supabase_utils.get_existing_jobs_from_supabase()

    new_job_ids_to_process = []
    already_in_db = 0
    for job_id in unique_linkedin_job_ids:
        if str(job_id) in job_ids_set:
            already_in_db += 1
            continue
        new_job_ids_to_process.append(str(job_id))

    # Record fetched and the dedup drop together, so the buckets partition `fetched`
    # instead of leaving "not saved" to mean three different things.
    if outcome is not None:
        outcome.record_query(fetched=len(unique_linkedin_job_ids), already_in_db=already_in_db)


    logging.info(f"Found {len(unique_linkedin_job_ids)} unique scraped IDs.")

    logging.info(f"Found {len(job_ids_set)} existing IDs in Supabase.")

    logging.info(f"Identified {len(new_job_ids_to_process)} new job IDs to fetch details for.")

    if not new_job_ids_to_process:
    
        logging.info("No new job IDs to process after filtering.")
        return []

    if limit is not None and len(new_job_ids_to_process) > limit:
        logging.info(f"Truncating new_job_ids_to_process from {len(new_job_ids_to_process)} to {limit} to stay within source limit.")
        if outcome is not None:
            outcome.record_filtered("over_per_query_limit", len(new_job_ids_to_process) - limit)
        new_job_ids_to_process = new_job_ids_to_process[:limit]

    logging.info(f"\n--- Starting Phase 2: Fetching Job Details for {len(new_job_ids_to_process)} New IDs ---")
    detailed_new_jobs = []
    processed_count = 0

    ids_to_fetch = new_job_ids_to_process

    for job_id in ids_to_fetch:
        details = _fetch_linkedin_job_details(job_id)
        if details:
            if is_internship_role(details.get('job_title'), details.get('level')):
                logging.info(f"Skipping internship/thesis job: {details.get('job_title')} (ID: {job_id})")
                _filtered("internship")
                continue
            if is_freelance_role(details.get('job_title'), details.get('level')):
                logging.info(f"Skipping freelance/contract job: {details.get('job_title')} (ID: {job_id})")
                _filtered("freelance")
                continue
            company_title_key = (dedup.normalize_company(details.get('company')),
                                 dedup.normalize_title(details.get('job_title')))
            if all(company_title_key) and company_title_key in company_title_set:
                logging.info(f"Skipping repost (company/title already in DB): {details.get('job_title')} @ {details.get('company')} (ID: {job_id})")
                _filtered("repost_same_company_title")
                continue
            if all(company_title_key):
                company_title_set.add(company_title_key)
            description = details.get('description')
            if description and description.strip(): 
                if 'job_id' in details and details['job_id'] is not None:
                    detailed_new_jobs.append(details)
                    processed_count += 1
                else:
                    logging.warning(f"Fetched details for {job_id} but missing 'job_id' key. Skipping.")
                    _filtered("missing_job_id")
            else:
                logging.warning(f"Skipping job ID {job_id} due to missing or empty description.") 
                _filtered("no_description")
        else:
            logging.warning(f"Skipping job ID {job_id} as detail fetching failed or returned no data.") 
            _filtered("detail_fetch_failed")


    logging.info(f"--- Finished Phase 2: Successfully fetched details for {processed_count} new job(s) ---")
    return detailed_new_jobs

# --- Per-source runners --------------------------------------------------------
# One function per source, so a matrix leg can run exactly one of them (`--source`)
# and the daily run can still run them all. Each returns its SourceOutcome.

def run_linkedin() -> scrape_guard.SourceOutcome:
    logging.info("\n--- Starting LinkedIn Job Scraping ---")
    outcome = scrape_guard.SourceOutcome("linkedin")
    max_jobs_per_search = config.MAX_JOBS_PER_SEARCH.get("linkedin", getattr(config, 'DEFAULT_MAX_JOBS_PER_SEARCH', 10))
    for query in config.LINKEDIN_SEARCH_QUERIES:
        logging.info(f"Processing Search Query: '{query}'")

        new_linkedin_job_details = process_linkedin_query(query, config.LINKEDIN_LOCATION,
                                                          limit=max_jobs_per_search,
                                                          outcome=outcome)

        if new_linkedin_job_details:
            logging.info(f"Saving {len(new_linkedin_job_details)} new job(s) for query '{query}'")
            supabase_utils.save_jobs_to_supabase(new_linkedin_job_details)
            outcome.record_query(new=len(new_linkedin_job_details))
        else:
            logging.info(f"No new job details were fetched or processed for query '{query}'.")
    return outcome


def run_arbeitsagentur() -> scrape_guard.SourceOutcome:
    logging.info("\n--- Starting Bundesagentur für Arbeit Job Scraping ---")
    from sources import arbeitsagentur

    outcome = scrape_guard.SourceOutcome(arbeitsagentur.SOURCE)
    max_jobs_per_search = config.MAX_JOBS_PER_SEARCH.get(
        arbeitsagentur.SOURCE, getattr(config, 'DEFAULT_MAX_JOBS_PER_SEARCH', 10))
    for query in config.ARBEITSAGENTUR_SEARCH_QUERIES:
        logging.info(f"Processing Arbeitsagentur Search Query: '{query}'")

        new_jobs = arbeitsagentur.process_query(query, limit=max_jobs_per_search,
                                                outcome=outcome)
        if new_jobs:
            logging.info(f"Saving {len(new_jobs)} new job(s) for query '{query}'")
            supabase_utils.save_jobs_to_supabase(new_jobs)
            outcome.record_query(new=len(new_jobs))
        else:
            logging.info(f"No new job details were fetched or processed for query '{query}'.")
    return outcome


# Every source this script knows how to run. Adding a source (the ATS adapters in
# Step B.2) means adding one entry here and one matrix leg in run_all.yml.
SOURCE_RUNNERS = {
    "linkedin": run_linkedin,
    "arbeitsagentur": run_arbeitsagentur,
}


def run_sources(sources: list) -> list:
    """Run each named source in order and return their outcomes."""
    outcomes = []
    for source in sources:
        started = time.time()
        outcome = SOURCE_RUNNERS[source]()
        outcome.elapsed_seconds = time.time() - started
        outcomes.append(outcome)
    return outcomes


# --- Main Execution ---

def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape job postings into Supabase.")
    parser.add_argument(
        "--source", action="append", dest="sources", choices=sorted(SOURCE_RUNNERS),
        help="Run only this source. Repeatable. An explicit --source overrides "
             "config.SCRAPING_SOURCES, so a workflow matrix leg gets exactly the "
             "source it asked for. Omit to run every source in SCRAPING_SOURCES.",
    )
    args = parser.parse_args(argv)

    if args.sources:
        # De-duplicated, order preserved.
        sources = list(dict.fromkeys(args.sources))
        logging.info(f"Running only the requested source(s): {', '.join(sources)} "
                     f"(config.SCRAPING_SOURCES is {config.SCRAPING_SOURCES}).")
    else:
        sources = [s for s in config.SCRAPING_SOURCES if s in SOURCE_RUNNERS]
        unknown = [s for s in config.SCRAPING_SOURCES if s not in SOURCE_RUNNERS]
        if unknown:
            logging.warning(f"SCRAPING_SOURCES names {unknown}, which this script cannot run. Ignoring.")
        if not sources:
            logging.error("No runnable sources configured — nothing to scrape.")
            return 1

    outcomes = run_sources(sources)

    logging.info(f"\n{'='*20} Job scraping script finished {'='*20}")
    logging.info(f"Total new jobs saved across all queries: {sum(o.new for o in outcomes)}")

    scrape_guard.write_step_summary(outcomes)

    # A source that returns nothing at all is broken and must fail the run — that is
    # the whole lesson of Indeed dying quietly for twelve weeks. "Fetched plenty,
    # saved nothing new" is a normal day and stays at INFO.
    return scrape_guard.report(outcomes)


if __name__ == "__main__":
    sys.exit(main())
