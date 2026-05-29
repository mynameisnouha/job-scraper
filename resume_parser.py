import logging
import pdfplumber
import config
import json
import models
from llm_client import primary_client

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def extract_text_from_pdf(pdf_path):
    """
    Extracts text from a given PDF file.

    Args:
        pdf_path (str): The file path to the PDF resume.

    Returns:
        str: The extracted text content from the PDF.
    """
    logging.info(f"Extracting text from: {pdf_path}")
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # Extract the visible text
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
            
            # Extract embedded hyperlinks which are not captured by extract_text()
            if page.hyperlinks:
                for link in page.hyperlinks:
                    uri = link.get("uri")
                    if uri:
                        text += f"Embedded Link: {uri}\n"
    return text

def parse_resume_with_ai(resume_text):
    """
    Send resume text to an AI model and get structured information back.
    
    Args:
        resume_text (str): The plain text extracted from the resume
        
    Returns:
        str: JSON string of structured resume information
    """
    logging.info("Processing resume with AI model...")

    prompt = f"""Extract and return the structured resume information from the text below. 
    Only use what is explicitly stated in the text and do not infer or invent any details.
    
    CRITICAL: If any information is missing or not available in the text, use "NA" for that field. 
    This applies to all fields (e.g., summary, dates, location, links, etc.). 
    Do NOT leave fields empty or use empty strings.

    Resume text:
    {resume_text}
    """

    response_text = primary_client.generate_content(
        prompt=prompt,
        response_format=models.Resume,
    )
    return response_text

def main():
    """
    Main function to orchestrate the resume parsing process.
    Downloads the resume PDF from Supabase Storage, parses it with AI, 
    and saves the structured data to both local file and Supabase DB.
    """
    import io
    import os
    import supabase_utils

    pdf_file_path = "./resume.pdf"

    # 1. Try to download resume PDF from Supabase Storage
    pdf_bytes = supabase_utils.download_resume_from_storage("resume.pdf")

    if pdf_bytes:
        logging.info("Successfully downloaded resume.pdf from Supabase Storage.")
        with open(pdf_file_path, 'wb') as f:
            f.write(pdf_bytes)
    elif os.path.exists(pdf_file_path):
        logging.info(f"Supabase Storage download failed. Using local file: {pdf_file_path}")
    else:
        logging.error("Could not find resume.pdf in Supabase Storage or locally.")
        logging.error("Please upload your resume.pdf to the 'resumes' bucket in your Supabase Storage dashboard.")
        return

    resume_text = extract_text_from_pdf(pdf_file_path)
    if not resume_text:
        logging.error("Failed to extract text. Exiting.")
        return

    parsed_resume_details_str = parse_resume_with_ai(resume_text)
    if not parsed_resume_details_str:
        logging.error("Failed to parse resume. Exiting.")
        return

    try:
        # Convert the JSON string response to a dictionary
        resume_data_dict = json.loads(parsed_resume_details_str)
        
        # Recursive function to replace empty values or None with "NA"
        def replace_empty_with_na(data):
            if isinstance(data, dict):
                return {k: replace_empty_with_na(v) for k, v in data.items()}
            elif isinstance(data, list):
                return[replace_empty_with_na(i) for i in data]
            elif data == "" or data is None:
                return "NA"
            return data

        resume_data_dict = replace_empty_with_na(resume_data_dict)

    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON response from AI: {e}")
        logging.error(f"Raw response: {parsed_resume_details_str}")
        return

    save_success = supabase_utils.save_base_resume(resume_data_dict)
    if save_success:
        logging.info("Successfully saved parsed resume to Supabase database.")
    else:
        logging.warning("Failed to save parsed resume to Supabase database.")

    output_path = config.BASE_RESUME_PATH
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(resume_data_dict, f, indent=4)
        logging.info(f"Successfully saved parsed resume to local file: {output_path}")
    except Exception as e:
        logging.error(f"Error saving resume to {output_path}: {e}")

    if pdf_bytes and os.path.exists(pdf_file_path):
        try:
            os.remove(pdf_file_path)
            logging.info(f"Cleaned up temporary file: {pdf_file_path}")
        except Exception as e:
            logging.warning(f"Could not clean up {pdf_file_path}: {e}")

    logging.info("Resume processing finished.")


if __name__ == "__main__":
    logging.info("Starting resume processing...")
    main()