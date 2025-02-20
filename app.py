import streamlit as st
import os
import json
import requests
from bs4 import BeautifulSoup
import PyPDF2
from dotenv import load_dotenv
import io
from fpdf import FPDF  # for PDF generation
import markdown  # to convert markdown to HTML

# -------------------------------
# Load environment variables from .env file.
# -------------------------------
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")
app_users_json = os.getenv("APP_USERS")

if not openai_api_key:
    st.error("OPENAI_API_KEY not found in secrets.")
    st.stop()

if not app_users_json:
    st.error("APP_USERS not defined in secrets.")
    st.stop()

try:
    app_users = json.loads(app_users_json)
except Exception as e:
    st.error("APP_USERS secret is not valid JSON.")
    st.stop()

# -------------------------------
# Simple login mechanism for multiple users.
# -------------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("Login")
    st.markdown("<h3>Enter your username and password to access the Interview Helper App</h3>", unsafe_allow_html=True)
    username_input = st.text_input("Username")
    password_input = st.text_input("Password", type="password")
    if st.button("Login"):
        if username_input in app_users and password_input == app_users[username_input]:
            st.session_state.logged_in = True
        else:
            st.error("Invalid username or password.")
    st.stop()

# -------------------------------
# Custom CSS for styling.
# -------------------------------
st.markdown(
    """
    <style>
    body {
        background-color: #ffffff;
        font-family: 'Segoe UI', sans-serif;
        color: #2c3e50;
    }
    .reportview-container .main {
        background: #ffffff;
        border-radius: 10px;
        padding: 20px;
    }
    h1 {
        text-align: center;
        color: #2c3e50;
    }
    h3 {
        text-align: center;
        color: #2c3e50;
        margin-bottom: 0;
    }
    .step-title {
        color: #2c3e50;
        border-bottom: 2px solid #d4af37;
        padding-bottom: 5px;
        margin-bottom: 10px;
    }
    .card {
        background-color: #ffffff;
        border: 2px solid #d4af37;
        border-radius: 10px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    .nav-buttons {
        margin-top: 20px;
    }
    .nav-buttons > div {
        text-align: center;
    }
    .button {
        background-color: #d4af37;
        color: #ffffff;
        border: none;
        padding: 10px 20px;
        border-radius: 5px;
        font-size: 1rem;
        cursor: pointer;
        margin: 5px;
    }
    .button:hover {
        background-color: #bfa15d;
    }
    .output {
        background: #ecf0f1;
        padding: 15px;
        border-radius: 5px;
        white-space: pre-wrap;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# -------------------------------
# Initialize OpenAI client.
# -------------------------------
from openai import OpenAI
client = OpenAI(api_key=openai_api_key)

# -------------------------------
# Helper: Extract text from PDF.
# -------------------------------
def extract_text_from_pdf(file) -> str:
    try:
        pdf_reader = PyPDF2.PdfReader(file)
        text = ""
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text
        return text
    except Exception as e:
        st.error(f"Error processing PDF: {e}")
        return ""

# -------------------------------
# Helper: Scrape job description from a URL.
# -------------------------------
def scrape_job_description(url: str):
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return None, f"Failed to retrieve URL (Status code: {response.status_code})"
        soup = BeautifulSoup(response.text, "html.parser")
        selectors = [
            {"id": "jobDescriptionText"},
            {"class": "job-description"},
            {"class": "jobDescription"},
            {"class": "description"}
        ]
        job_desc = None
        for selector in selectors:
            if "id" in selector:
                element = soup.find(id=selector["id"])
            elif "class" in selector:
                element = soup.find(class_=selector["class"])
            if element:
                job_desc = element.get_text(separator="\n", strip=True)
                if len(job_desc) > 50:
                    break
        if not job_desc or len(job_desc) < 50:
            job_desc = soup.get_text(separator="\n", strip=True)
        return job_desc, None
    except Exception as e:
        return None, str(e)

# -------------------------------
# Helper: Clean text for PDF output.
# -------------------------------
def clean_text(text: str) -> str:
    replacements = {
        "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-"
    }
    for orig, repl in replacements.items():
        text = text.replace(orig, repl)
    return text

# -------------------------------
# Navigation: Reset session state.
# -------------------------------
def reset_state():
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state.step = 1

# -------------------------------
# Multi-page navigation using session state.
# -------------------------------
if "step" not in st.session_state:
    st.session_state.step = 1

st.title("Interview Helper App")
st.markdown("<h3>Job Search never been easier</h3>", unsafe_allow_html=True)
st.markdown("<hr>", unsafe_allow_html=True)

# -------------------------------
# Page 1: Upload CV & generate CV summary.
# -------------------------------
if st.session_state.step == 1:
    st.markdown(
        """
        <div class='card'>
          <h2 class='step-title'>Step 1: Upload Your CV</h2>
          <p>Please upload your CV (PDF format). We will analyze it and provide a concise summary (max 150 tokens, no more than 2 paragraphs) of your strengths and weaknesses.</p>
        </div>
        """, unsafe_allow_html=True
    )
    uploaded_cv = st.file_uploader("Upload your CV (PDF format)", type=["pdf"])
    if uploaded_cv is not None:
        with st.spinner("Extracting text from CV..."):
            cv_text = extract_text_from_pdf(uploaded_cv)
        if cv_text:
            st.success("CV text extracted successfully!")
            if "cv_summary" not in st.session_state:
                with st.spinner("Generating concise CV summary..."):
                    prompt = (
                        "Based on the following candidate CV, provide a concise summary"
                        "highlighting the candidate's top strengths and weaknesses. List them as bullet points.\n\nCandidate CV:\n"
                        + cv_text
                    )
                    try:
                        response = client.chat.completions.create(
                            model="gpt-4o",
                            messages=[
                                {"role": "system", "content": "You are a helpful interview assistant. Please be concise."},
                                {"role": "user", "content": prompt}
                            ],
                            response_format={"type": "text"},
                            temperature=0.7,
                            max_completion_tokens=1000,
                            top_p=1,
                            frequency_penalty=0,
                            presence_penalty=0
                        )
                        st.session_state.cv_summary = response.choices[0].message.content
                    except Exception as e:
                        st.error(f"Error generating CV summary: {e}")
            if "cv_summary" in st.session_state:
                st.markdown("<div class='card'><h3>CV Summary (Strengths & Weaknesses)</h3></div>", unsafe_allow_html=True)
                st.markdown(st.session_state.cv_summary, unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.button("Back", key="back1", disabled=True)
    with col2:
        if st.button("Next", key="next1"):
            if uploaded_cv is not None and "cv_summary" in st.session_state:
                st.session_state.step = 2
            else:
                st.error("Please upload your CV and generate its summary before proceeding.")
    with col3:
        if st.button("Start Again", key="restart1"):
            reset_state()

# -------------------------------
# Page 2: Provide Job Description & generate job summary.
# -------------------------------
elif st.session_state.step == 2:
    st.markdown(
        """
        <div class='card'>
          <h2 class='step-title'>Step 2: Provide Job Description</h2>
          <p>Please provide the job description either by entering a URL or typing it manually. We will refine it into a concise summary (max 150 tokens, no more than 2 paragraphs) highlighting key responsibilities and requirements.</p>
        </div>
        """, unsafe_allow_html=True
    )
    job_input_method = st.radio("Choose how to provide the job description:", ("Job URL", "Manual Input"), key="job_method")
    # Clear any previous job_summary when switching methods.
    if "job_summary" in st.session_state:
        del st.session_state.job_summary
    job_description = ""
    if job_input_method == "Job URL":
        job_url = st.text_input("Enter the job URL")
        if st.button("Scrape & Refine", key="scrape"):
            if job_url:
                with st.spinner("Scraping job description..."):
                    scraped_text, error = scrape_job_description(job_url)
                if error:
                    st.error(f"Error scraping job description: {error}")
                    st.info("Please try the Manual Input option.")
                    if "job_summary" in st.session_state:
                        del st.session_state.job_summary
                else:
                    st.success("Job description scraped successfully!")
                    with st.spinner("Generating concise job summary..."):
                        prompt = (
                            "Based on the following job description, provide a concise summary (max 150 tokens, no more than 2 paragraphs) "
                            "highlighting the key responsibilities, requirements, and skills needed for the role. List them as bullet points.\n\nJob Description:\n"
                            + scraped_text +
                            "\n\nPlease ensure your response is in at most 2 paragraphs."
                        )
                        try:
                            response = client.chat.completions.create(
                                model="gpt-4o",
                                messages=[
                                    {"role": "system", "content": "You are a helpful interview assistant. Please be concise."},
                                    {"role": "user", "content": prompt}
                                ],
                                response_format={"type": "text"},
                                temperature=0.7,
                                max_completion_tokens=1000,
                                top_p=1,
                                frequency_penalty=0,
                                presence_penalty=0
                            )
                            st.session_state.job_summary = response.choices[0].message.content
                        except Exception as e:
                            st.error(f"Error generating job summary: {e}")
            else:
                st.error("Please provide a job URL.")
    else:
        job_description = st.text_area("Enter the job description manually", height=300)
        if job_description:
            with st.spinner("Generating concise job summary..."):
                prompt = (
                    "Based on the following job description, provide a concise summary (max 150 tokens, no more than 2 paragraphs) "
                    "highlighting the key responsibilities, requirements, and skills needed for the role. List them as bullet points.\n\nJob Description:\n"
                    + job_description +
                    "\n\nPlease ensure your response is in at most 2 paragraphs."
                )
                try:
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": "You are a helpful interview assistant. Please be concise."},
                            {"role": "user", "content": prompt}
                        ],
                        response_format={"type": "text"},
                        temperature=0.7,
                        max_completion_tokens=1000,
                        top_p=1,
                        frequency_penalty=0,
                        presence_penalty=0
                    )
                    st.session_state.job_summary = response.choices[0].message.content
                except Exception as e:
                    st.error(f"Error generating job summary: {e}")
    if "job_summary" in st.session_state:
        st.markdown("<div class='card'><h3>Refined Job Summary</h3></div>", unsafe_allow_html=True)
        st.markdown(st.session_state.job_summary, unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Back", key="back2"):
            st.session_state.step = 1
    with col2:
        if st.button("Next", key="next2"):
            if "job_summary" in st.session_state:
                st.session_state.step = 3
            else:
                st.error("Please provide a job description and generate its summary before proceeding.")
    with col3:
        if st.button("Start Again", key="restart2"):
            reset_state()

# -------------------------------
# Page 3: Generate candidate fit score, interview questions & PDF report.
# -------------------------------
elif st.session_state.step == 3:
    st.markdown(
        """
        <div class='card'>
          <h2 class='step-title'>Step 3: Interview Questions, Guidance & PDF Report</h2>
          <p>Review your CV and job summaries below. Then click the button to generate: (a) a candidate fit score (1-100 with brief explanation), and (b) targeted interview questions with concise guidance (max 100 tokens per question, no more than 2 paragraphs total). A professionally formatted Interview Prep PDF will be generated for download.</p>
        </div>
        """, unsafe_allow_html=True
    )
    st.markdown("<div class='card'><h3>CV Summary</h3></div>", unsafe_allow_html=True)
    st.markdown(st.session_state.cv_summary, unsafe_allow_html=True)
    st.markdown("<div class='card'><h3>Job Summary</h3></div>", unsafe_allow_html=True)
    st.markdown(st.session_state.job_summary, unsafe_allow_html=True)
    
    # Generate candidate fit score if not present.
    if "fit_score" not in st.session_state:
        with st.spinner("Generating candidate fit score..."):
            prompt_fit = (
                "Based on the following CV summary and job summary, provide a candidate fit score (1 to 100) with a brief explanation (max 1 paragraph). "
                "Please ensure your response is in at most 2 paragraphs.\n\nCV Summary:\n" 
                + st.session_state.cv_summary + "\n\nJob Summary:\n" + st.session_state.job_summary
            )
            try:
                response_fit = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "You are a helpful interview assistant. Please be concise."},
                        {"role": "user", "content": prompt_fit}
                    ],
                    response_format={"type": "text"},
                    temperature=0.7,
                    max_completion_tokens=1000,
                    top_p=1,
                    frequency_penalty=0,
                    presence_penalty=0
                )
                st.session_state.fit_score = response_fit.choices[0].message.content
            except Exception as e:
                st.error(f"Error generating candidate fit score: {e}")
    if "fit_score" in st.session_state:
        st.markdown("<div class='card'><h3>Candidate Fit Score</h3></div>", unsafe_allow_html=True)
        st.markdown(st.session_state.fit_score, unsafe_allow_html=True)
    
    if st.button("Generate Interview Questions & PDF Report", key="final_gen"):
        with st.spinner("Generating interview questions, guidance and PDF report..."):
            prompt_questions = (
                "Based on the following concise CV summary and job summary, generate a list of interview questions that assess the candidate's fit for the role. "
                "For each question, provide concise guidance in bullet points (max 100 tokens per question, no more than 2 paragraphs total) indicating the key qualities or skills to emphasize in the answer. "
                "Also, include a candidate fit score (1-100) with a brief explanation. Do not include extra commentary.\n\n"
                "CV Summary:\n" + st.session_state.cv_summary + "\n\n"
                "Job Summary:\n" + st.session_state.job_summary
            )
            try:
                response_questions = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "You are a helpful interview assistant. Please be concise and ensure your response is in at most 2 paragraphs."},
                        {"role": "user", "content": prompt_questions}
                    ],
                    response_format={"type": "text"},
                    temperature=0.7,
                    max_completion_tokens=1000,
                    top_p=1,
                    frequency_penalty=0,
                    presence_penalty=0
                )
                st.session_state.interview_output = response_questions.choices[0].message.content
            except Exception as e:
                st.error(f"Error generating interview questions: {e}")
            
            # Create PDF document using fpdf with colors.
            pdf = FPDF()
            pdf.add_page()
            pdf.compress = False  # Disable compression to avoid encoding issues.
            
            # Title with dark blue color.
            pdf.set_text_color(44, 62, 80)
            pdf.set_font("Arial", "B", 16)
            pdf.cell(0, 10, "Interview Preparation Document", ln=1, align="C")
            pdf.ln(5)
            
            # Job Summary section in blue.
            job_summary_clean = clean_text(st.session_state.job_summary)
            pdf.set_text_color(41, 128, 185)
            pdf.set_font("Arial", "B", 12)
            pdf.cell(0, 10, "Job Needs (Refined Job Summary):", ln=1)
            pdf.set_font("Arial", "", 11)
            for line in job_summary_clean.split("\n"):
                pdf.multi_cell(0, 8, line)
            pdf.ln(5)
            
            # Candidate Fit Score section in green.
            fit_score_clean = clean_text(st.session_state.fit_score)
            pdf.set_text_color(39, 174, 96)
            pdf.set_font("Arial", "B", 12)
            pdf.cell(0, 10, "Candidate Fit Score:", ln=1)
            pdf.set_font("Arial", "", 11)
            for line in fit_score_clean.split("\n"):
                pdf.multi_cell(0, 8, line)
            pdf.ln(5)
            
            # Interview Questions & Guidance in purple.
            interview_output_clean = clean_text(st.session_state.interview_output)
            pdf.set_text_color(142, 68, 173)
            pdf.set_font("Arial", "B", 12)
            pdf.cell(0, 10, "Interview Questions & Guidance:", ln=1)
            pdf.set_font("Arial", "", 11)
            for line in interview_output_clean.split("\n"):
                pdf.multi_cell(0, 8, line)
            
            # Generate PDF output as a string, clean it and then encode with error replacement.
            pdf_output_str = pdf.output(dest="S")
            pdf_output_str = clean_text(pdf_output_str)
            pdf_bytes = pdf_output_str.encode("latin1", "replace")
            
            st.markdown("<div class='card'><h3>Interview Questions & Guidance</h3></div>", unsafe_allow_html=True)
            html_interview = markdown.markdown(st.session_state.interview_output)
            st.markdown(f"<div class='output'>{html_interview}</div>", unsafe_allow_html=True)
            
            st.download_button(
                label="Download Interview Prep PDF",
                data=pdf_bytes,
                file_name="Interview_Prep.pdf",
                mime="application/pdf"
            )
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Back", key="back3"):
            st.session_state.step = 2
    with col2:
        st.write("")  # spacer
    with col3:
        if st.button("Start Again", key="restart3"):
            reset_state()
