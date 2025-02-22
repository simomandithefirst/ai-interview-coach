import streamlit as st
import os
import json
import requests
from bs4 import BeautifulSoup
import PyPDF2
from dotenv import load_dotenv
import io
import markdown as md  # using alias 'md'
from openai import OpenAI

# -------------------------------
# Load environment variables and initialize client
# -------------------------------
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")
app_users_json = os.getenv("APP_USERS")

if not openai_api_key:
    st.error("OPENAI_API_KEY not found in environment variables.")
    st.stop()
if not app_users_json:
    st.error("APP_USERS not defined in environment variables.")
    st.stop()
try:
    app_users = json.loads(app_users_json)
except Exception as e:
    st.error("APP_USERS secret is not valid JSON.")
    st.stop()

client = OpenAI(api_key=openai_api_key)

# -------------------------------
# Global CSS and Page Configuration
# -------------------------------
st.set_page_config(page_title="Career Catalyst", layout="wide")

st.markdown(
    """
    <style>
    body {
        background-color: #f7f7f7;
        font-family: 'Segoe UI', sans-serif;
        color: #2c3e50;
    }
    .main {
        background: #ffffff;
        border-radius: 10px;
        padding: 20px;
    }
    h1, h2, h3 { text-align: center; }
    .module-title {
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
    .button:hover { background-color: #bfa15d; }
    .output {
        background: #ecf0f1;
        padding: 15px;
        border-radius: 5px;
        white-space: pre-wrap;
    }
    [data-testid="stSidebar"] {
        right: 0;
        left: auto;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# -------------------------------
# Session State Initialization
# -------------------------------
if "step" not in st.session_state:
    st.session_state.step = 0  # start at Landing Page
for key in ["cv_text", "cv_analysis", "jd_text", "jd_analysis", 
            "fit_analysis", "cv_improvement", "interview_output", "parsed_questions", "jd_scraped"]:
    if key not in st.session_state:
        st.session_state[key] = None
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

# -------------------------------
# Authentication
# -------------------------------
if not st.session_state.get("logged_in"):
    st.title("Login")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        if username in app_users and password == app_users[username]:
            st.session_state.logged_in = True
            st.success("Logged in successfully!")
            st.rerun()
        else:
            st.error("Invalid username or password.")
    st.stop()

# -------------------------------
# Navigation Functions
# -------------------------------
def go_to_module(new_module: int):
    st.session_state.step = new_module
    st.rerun()

def reset_files():
    for key in ["cv_text", "jd_text", "jd_analysis", "cv_analysis"]:
        st.session_state.pop(key, None)
    go_to_module(1)

def reset_state():
    for key in list(st.session_state.keys()):
        if key not in ["logged_in"]:
            st.session_state.pop(key)
    st.session_state.step = 0
    st.rerun()

# -------------------------------
# Utility Functions
# -------------------------------
def render_card(content: str):
    # Convert markdown to HTML using the markdown library
    html_content = md.markdown(content)
    st.markdown(f"<div class='card'>{html_content}</div>", unsafe_allow_html=True)

def clean_markdown_output(text: str) -> str:
    return text.replace("```markdown", "").replace("```", "")

def extract_text_from_pdf(file) -> str:
    try:
        reader = PyPDF2.PdfReader(file)
        text = "\n".join(page.extract_text() for page in reader.pages if page.extract_text())
        return text.strip()
    except Exception as e:
        st.error(f"Error processing PDF: {e}")
        return ""

def scrape_job_description(url: str):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        selectors = [
            {"id": "jobDescriptionText"},
            {"class": "job-description"},
            {"class": "jobDescription"},
            {"class": "description"}
        ]
        for selector in selectors:
            element = soup.find(**selector)
            if element and len(element.get_text(strip=True)) > 200:
                return element.get_text(separator="\n", strip=True), None
        return soup.get_text(separator="\n", strip=True), None
    except Exception as e:
        return None, str(e)

def clean_text(text: str) -> str:
    replacements = {
        "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-"
    }
    for orig, repl in replacements.items():
        text = text.replace(orig, repl)
    return text

def format_question(q_obj: dict):
    summary = q_obj.get("question", "").strip()
    guidelines = q_obj.get("guidelines", "").strip()
    fit_score = q_obj.get("fit_score", "")
    details = f"**Guidelines:** {guidelines}\n\n**Candidate Fit Score:** {fit_score}"
    return summary, details

# -------------------------------
# Prompt Templates
# -------------------------------
CV_ANALYSIS_PROMPT = """Analyze the following CV and provide insightful observations that go beyond simply listing its content. Focus on identifying unique strengths and areas for improvement that the candidate may not have realized.

Observations:
- Unique strengths:
- Areas for improvement:

CV Content:
{cv_text}"""

JD_ANALYSIS_PROMPT = """Analyze the following job description and organize the key requirements and challenges in markdown. Ensure the output is well-formatted with clear bullet points.

Key Requirements:
1. Leadership & Teamwork
2. Technical Requirements
3. Education & Qualifications
4. Soft Skills

Job Description:
{jd_text}"""

FIT_ANALYSIS_PROMPT = """Based on the following raw CV and Job Description, generate a structured fit analysis.

Overall Fit Score: [Score out of 100]
Rationale: [Brief explanation]

Area-by-Area Analysis:
- Technical Skills: [Strengths and gaps]
- Leadership: [Strengths and gaps]
- Qualifications: [Match or discrepancies]

CV:
{cv_text}

Job Description:
{jd_text}
"""

CV_ENHANCEMENT_PROMPT = """Based solely on the following CV content, identify phrases that can be rephrased to better match the provided job description and increase your chances of passing automated screening. For each suggestion, provide a bullet point that includes:
- The exact phrase from the CV that should be changed.
- The recommended new phrasing (do not add any new skills or fabricate experiences).
- A brief rationale explaining why this change is needed.

Do not suggest adding any new information—only reframe or reorder the existing content if possible. If no changes are needed, simply state that the CV is already well-aligned.

CV Content:
{cv_text}

Job Description:
{jd_text}
"""

INTERVIEW_QUESTIONS_PROMPT = """Based on the following CV and job description, generate interview questions likely to come up during an interview for the position grouped by category.
The categories must be exactly: "Technical", "Behavioral", "CV Related".

For each category, output a list of 10 question objects. Each question object must have the following keys:
- "question": A string representing the interview question.
- "guidelines": A string with concise guidance in bullet points (max 100 tokens per question).
- "fit_score": A number representing the candidate's fit score (1-100) with a brief explanation.

Return only a valid JSON object with exactly three keys: "Technical", "Behavioral", and "CV Related". Do not include any extra text. Your output must be valid JSON.

CV:
{cv_text}

Job Description:
{jd_text}
"""

FEEDBACK_PROMPT = """Evaluate the following interview answer using the appropriate framework (accuracy/soundness, STAR, etc.).
Your response must begin with a clear pass/fail indicator using HTML: if the answer is good, start with '<span style="color: green;">PASS</span>'; if not, start with '<span style="color: red;">FAIL</span>'.
Then, provide detailed feedback and suggestions for improvement.

Question: {question}
Answer: {answer}
"""

# -------------------------------
# Sidebar Navigation (Visible in Modules 1+)
# -------------------------------
if st.session_state.step > 0:
    with st.sidebar:
        st.markdown("## Modules")
        modules = [
            ("Module 1: CV Analysis", 1, st.session_state.cv_analysis is not None),
            ("Module 2: Job Analysis", 2, st.session_state.jd_analysis is not None),
            ("Module 3: Fit Analysis", 3, st.session_state.fit_analysis is not None),
            ("Module 4: CV Improvement", 4, st.session_state.cv_improvement is not None),
            ("Module 5: Interview Prep", 5, st.session_state.interview_output is not None),
            ("Module 6: Practice Interview", 6, st.session_state.parsed_questions is not None and bool(st.session_state.parsed_questions))
        ]
        for label, mod_num, completed in modules:
            indicator = "✅" if completed else ""
            if st.button(f"{label} {indicator}", key=f"module_{mod_num}"):
                go_to_module(mod_num)
        st.markdown("---")
        if st.button("Reset App", use_container_width=True):
            reset_state()

# -------------------------------
# Landing Page (Module 0)
# -------------------------------
if st.session_state.step == 0:
    st.title("Career Catalyst")
    st.markdown("## Welcome to Career Catalyst")
    st.markdown("""
**Career Catalyst** is your personal career enhancement platform. Whether you're applying for a job or preparing for an interview, our tools will help you:
- **Analyze Your CV:** Identify your unique strengths and potential areas for improvement.
- **Break Down Job Descriptions:** Understand key requirements and challenges.
- **Assess Your Fit:** Receive personalized tips and a fit score.
- **Enhance Your CV:** Get actionable suggestions to better tailor your CV.
- **Practice Interviews:** Prepare with targeted interview questions and receive instant feedback.
    """)
    if st.button("Get Started"):
        st.session_state.step = 1
        st.rerun()

# -------------------------------
# Module 1: CV Analysis
# -------------------------------
if st.session_state.step == 1:
    st.header("Module 1: CV Analysis")
    st.markdown("**Step 1:** Upload your CV (PDF) for analysis. We will extract text and highlight your strengths and areas to improve.")
    
    # Upload or use current CV
    if st.session_state.cv_text:
        cv_option = st.radio("CV Options:", ["Use current CV", "Upload a new CV"], key="cv_option")
        if cv_option == "Upload a new CV":
            uploaded_cv = st.file_uploader("Upload your CV (PDF)", type=["pdf"], key="cv_upload1")
            if uploaded_cv:
                cv_text = extract_text_from_pdf(uploaded_cv)
                st.text_area("Extracted CV Text:", value=cv_text, height=200, disabled=True)
                st.session_state.cv_text = cv_text
            else:
                cv_text = st.session_state.cv_text
        else:
            cv_text = st.session_state.cv_text
            st.text_area("Current CV Text:", value=cv_text, height=200, disabled=True)
    else:
        uploaded_cv = st.file_uploader("Upload your CV (PDF)", type=["pdf"], key="cv_upload1")
        if uploaded_cv:
            cv_text = extract_text_from_pdf(uploaded_cv)
            st.text_area("Extracted CV Text:", value=cv_text, height=200, disabled=True)
            st.session_state.cv_text = cv_text
        else:
            cv_text = ""
            st.warning("Please upload a valid CV.")
    
    if cv_text and st.button("Run CV Analysis", key="run_cv_analysis"):
        with st.spinner("Analyzing your CV..."):
            try:
                prompt = CV_ANALYSIS_PROMPT.format(cv_text=cv_text)
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7,
                    max_tokens=1500
                )
                result = response.choices[0].message.content.strip()
                if result and len(result) > 20:
                    st.session_state.cv_analysis = result
                else:
                    st.error("CV Analysis returned an unexpected result.")
            except Exception as e:
                st.error(f"Error analyzing CV: {e}")
    
    if st.session_state.get("cv_analysis"):
        st.markdown("### Analysis Result")
        render_card(st.session_state.cv_analysis)
        if st.button("Next: Job Analysis", key="next_to_jd"):
            go_to_module(2)

# -------------------------------
# Module 2: Job Analysis
# -------------------------------
elif st.session_state.step == 2:
    st.header("Module 2: Job Analysis")
    st.markdown("**Step 2:** Provide the Job Description either by entering a URL (to scrape) or by pasting it manually.")
    
    # Two-column layout for job description input
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Job URL")
        job_url = st.text_input("Enter Job Posting URL", key="jd_url")
        if job_url:
            if st.button("Scrape Job Posting", key="scrape_jd"):
                with st.spinner("Scraping job posting..."):
                    scraped_text, error = scrape_job_description(job_url)
                    if error:
                        st.error(f"Scraping error: {error}")
                    elif scraped_text:
                        st.session_state.jd_scraped = scraped_text  # Save the scraped text in session state
                        st.text_area("Scraped Job Description:", value=scraped_text, height=200, disabled=True)
    with col2:
        st.subheader("Manual Input")
        manual_jd = st.text_area("Paste the Job Description here", key="jd_manual", height=200)
    
    # Prepare options: use scraped text if available, otherwise use manual input if provided.
    options = {}
    if st.session_state.jd_scraped:
        options["Scraped"] = st.session_state.jd_scraped
    if manual_jd.strip():
        options["Manual"] = manual_jd.strip()
    
    if options:
        choice = st.radio("Select the Job Description source", list(options.keys()), key="jd_choice")
        jd_text = options[choice]
    else:
        jd_text = ""
        st.warning("Please provide a job description via URL or manual input.")
    
    if jd_text and st.button("Run Job Analysis", key="run_jd_analysis"):
        with st.spinner("Analyzing job description..."):
            try:
                prompt = JD_ANALYSIS_PROMPT.format(jd_text=jd_text)
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7,
                    max_tokens=1500
                )
                result = response.choices[0].message.content.strip()
                if result and len(result) > 20:
                    st.session_state.jd_analysis = result
                    st.session_state.jd_text = jd_text
                else:
                    st.error("Job Analysis returned an unexpected result.")
            except Exception as e:
                st.error(f"Error analyzing job description: {e}")
    
    if st.session_state.get("jd_analysis"):
        st.markdown("### Job Analysis Result")
        cleaned = clean_markdown_output(st.session_state.jd_analysis)
        render_card(cleaned)
        if st.button("Next: Fit Analysis", key="next_to_fit"):
            go_to_module(3)

# -------------------------------
# Module 3: Fit Analysis & Tips
# -------------------------------
elif st.session_state.step == 3:
    st.header("Module 3: Fit Analysis & Tips")
    st.markdown("**Step 3:** Let’s see how well your CV fits the job. Upload new files if needed or use your current CV and Job Description.")
    
    # Option to reupload or use current files
    option_files = st.radio("Select input option:", ["Use current files", "Upload new files"], key="files_option3")
    if option_files == "Upload new files":
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Upload CV")
            new_cv = st.file_uploader("Upload CV (PDF)", type=["pdf"], key="cv_upload3")
            if new_cv:
                cv_text = extract_text_from_pdf(new_cv)
                st.text_area("Extracted CV:", value=cv_text, height=200, disabled=True)
            else:
                cv_text = ""
        with col2:
            st.subheader("Upload Job Description")
            new_jd = st.text_area("Enter Job Description or URL", key="jd_upload3", height=200)
            if new_jd.strip().startswith("http"):
                if st.button("Scrape Job Posting", key="scrape_jd3"):
                    with st.spinner("Scraping..."):
                        scraped_new_jd, error = scrape_job_description(new_jd.strip())
                        if error:
                            st.error(f"Scraping error: {error}")
                        else:
                            new_jd = scraped_new_jd
            st.text_area("Job Description:", value=new_jd, height=200, disabled=True)
            jd_text = new_jd.strip()
    else:
        cv_text = st.session_state.cv_text
        jd_text = st.session_state.jd_text
    
    if cv_text and jd_text and st.button("Run Fit Analysis", key="run_fit_analysis"):
        with st.spinner("Calculating fit analysis..."):
            try:
                prompt = FIT_ANALYSIS_PROMPT.format(cv_text=cv_text, jd_text=jd_text)
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7,
                    max_tokens=1500
                )
                result = response.choices[0].message.content.strip()
                if result and len(result) > 20:
                    st.session_state.fit_analysis = result
                else:
                    st.error("Fit Analysis returned an unexpected result.")
            except Exception as e:
                st.error(f"Error during fit analysis: {e}")
    
    if st.session_state.get("fit_analysis"):
        st.markdown("### Fit Analysis & Tips")
        render_card(st.session_state.fit_analysis)
        if st.button("Next: CV Improvement Suggestions", key="next_to_cvimp"):
            go_to_module(4)

# -------------------------------
# Module 4: CV Improvement Suggestions
# -------------------------------
elif st.session_state.step == 4:
    st.header("Module 4: CV Improvement Suggestions")
    st.markdown("**Step 4:** Get actionable suggestions to reframe your CV for a better job fit. You can update your files if needed.")
    
    option_files = st.radio("Select input option:", ["Use current files", "Upload new files"], key="files_option4")
    if option_files == "Upload new files":
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Upload CV")
            new_cv = st.file_uploader("Upload CV (PDF)", type=["pdf"], key="cv_upload4")
            if new_cv:
                cv_text = extract_text_from_pdf(new_cv)
                st.text_area("Extracted CV:", value=cv_text, height=200, disabled=True)
            else:
                cv_text = ""
        with col2:
            st.subheader("Upload Job Description")
            new_jd = st.text_area("Enter Job Description or URL", key="jd_upload4", height=200)
            if new_jd.strip().startswith("http"):
                if st.button("Scrape Job Posting", key="scrape_jd4"):
                    with st.spinner("Scraping..."):
                        scraped_new_jd, error = scrape_job_description(new_jd.strip())
                        if error:
                            st.error(f"Scraping error: {error}")
                        else:
                            new_jd = scraped_new_jd
            st.text_area("Job Description:", value=new_jd, height=200, disabled=True)
            jd_text = new_jd.strip()
    else:
        cv_text = st.session_state.cv_text
        jd_text = st.session_state.jd_text
    
    if cv_text and jd_text and st.button("Generate CV Improvement Suggestions", key="run_cvimp"):
        with st.spinner("Generating suggestions..."):
            try:
                prompt = CV_ENHANCEMENT_PROMPT.format(cv_text=cv_text, jd_text=jd_text)
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7,
                    max_tokens=1000
                )
                result = response.choices[0].message.content.strip()
                if result and len(result) > 20:
                    st.session_state.cv_improvement = result
                else:
                    st.error("CV Improvement Suggestions returned an unexpected result.")
            except Exception as e:
                st.error(f"Error generating suggestions: {e}")
    
    if st.session_state.get("cv_improvement"):
        st.markdown("### CV Improvement Suggestions")
        render_card(st.session_state.cv_improvement)
        if st.button("Next: Interview Prep", key="next_to_int"):
            go_to_module(5)

# -------------------------------
# Module 5: Interview Questions & Guidance
# -------------------------------
elif st.session_state.step == 5:
    st.header("Module 5: Interview Questions & Guidance")
    st.markdown("**Step 5:** Generate interview questions based on your CV and job description. Use these to prepare for your interview.")
    
    option_files = st.radio("Select input option:", ["Use current files", "Upload new files"], key="files_option5")
    if option_files == "Upload new files":
        with st.expander("Upload New Files"):
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Upload CV")
                new_cv = st.file_uploader("Upload CV (PDF)", type=["pdf"], key="cv_upload5")
                if new_cv:
                    cv_text = extract_text_from_pdf(new_cv)
                    st.text_area("Extracted CV:", value=cv_text, height=200, disabled=True)
                else:
                    cv_text = ""
            with col2:
                st.subheader("Upload Job Description")
                new_jd = st.text_area("Enter Job Description or URL", key="jd_upload5", height=200)
                if new_jd.strip().startswith("http"):
                    if st.button("Scrape Job Posting", key="scrape_jd5"):
                        with st.spinner("Scraping..."):
                            scraped_new_jd, error = scrape_job_description(new_jd.strip())
                            if error:
                                st.error(f"Scraping error: {error}")
                            else:
                                new_jd = scraped_new_jd
                st.text_area("Job Description:", value=new_jd, height=200, disabled=True)
                jd_text = new_jd.strip()
    else:
        cv_text = st.session_state.cv_text
        jd_text = st.session_state.jd_text

    if st.button("Generate Interview Questions", key="gen_int_questions"):
        if not cv_text or not jd_text:
            st.error("Both CV and Job Description are required.")
        else:
            with st.spinner("Generating interview questions..."):
                try:
                    prompt = INTERVIEW_QUESTIONS_PROMPT.format(cv_text=cv_text, jd_text=jd_text)
                    messages = [{"role": "user", "content": prompt}]
                    functions = [{
                        "name": "get_interview_questions",
                        "description": "Return interview questions grouped by category in a JSON object. Each category's value is a list of question objects with keys 'question', 'guidelines', and 'fit_score'.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "Technical": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "question": {"type": "string"},
                                            "guidelines": {"type": "string"},
                                            "fit_score": {"type": "number"}
                                        },
                                        "required": ["question", "guidelines", "fit_score"]
                                    }
                                },
                                "Behavioral": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "question": {"type": "string"},
                                            "guidelines": {"type": "string"},
                                            "fit_score": {"type": "number"}
                                        },
                                        "required": ["question", "guidelines", "fit_score"]
                                    }
                                },
                                "CV Related": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "question": {"type": "string"},
                                            "guidelines": {"type": "string"},
                                            "fit_score": {"type": "number"}
                                        },
                                        "required": ["question", "guidelines", "fit_score"]
                                    }
                                }
                            },
                            "required": ["Technical", "Behavioral", "CV Related"]
                        }
                    }]
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=messages,
                        functions=functions,
                        function_call="auto",
                        temperature=0.7,
                        max_tokens=3000
                    )
                    message = response.choices[0].message
                    try:
                        if hasattr(message, "function_call") and message.function_call:
                            arguments = message.function_call.arguments
                        else:
                            arguments = message.content
                        arguments_clean = arguments.replace("\n", "\\n")
                        parsed = json.loads(arguments_clean)
                        st.session_state.parsed_questions = parsed
                        st.session_state.interview_output = json.dumps(parsed, indent=2)
                    except Exception as pe:
                        st.error("Failed to parse interview questions into JSON. Raw output: " + arguments)
                        st.session_state.parsed_questions = {}
                    
                    st.markdown("### Interview Questions by Category")
                    for category, qlist in st.session_state.parsed_questions.items():
                        st.subheader(category)
                        questions_formatted = "<br>".join("- " + format_question(q)[0] for q in qlist)
                        render_card(questions_formatted)
                except Exception as e:
                    st.error(f"Error generating interview questions: {e}")
    if st.button("Next: Practice Interview", key="next_to_practice"):
        go_to_module(6)

# -------------------------------
# Module 6: Practice Interviewing
# -------------------------------
elif st.session_state.step == 6:
    st.header("Module 6: Practice Interviewing")
    st.markdown("**Step 6:** Practice your interview skills. Select one of the generated questions or enter a custom one. Record or type your answer and receive immediate feedback.")
    
    # Option to override files if needed
    if st.checkbox("Upload new CV and Job Description", key="override_files6"):
        option_files = st.radio("Select input option:", ["Use current files", "Upload new files"], key="files_option6")
        if option_files == "Upload new files":
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Upload CV")
                new_cv = st.file_uploader("Upload CV (PDF)", type=["pdf"], key="cv_upload6")
                if new_cv:
                    cv_text = extract_text_from_pdf(new_cv)
                    st.text_area("Extracted CV:", value=cv_text, height=200, disabled=True)
                else:
                    cv_text = ""
            with col2:
                st.subheader("Upload Job Description")
                new_jd = st.text_area("Enter Job Description or URL", key="jd_upload6", height=200)
                if new_jd.strip().startswith("http"):
                    if st.button("Scrape Job Posting", key="scrape_jd6"):
                        with st.spinner("Scraping..."):
                            scraped_new_jd, error = scrape_job_description(new_jd.strip())
                            if error:
                                st.error(f"Scraping error: {error}")
                            else:
                                new_jd = scraped_new_jd
                st.text_area("Job Description:", value=new_jd, height=200, disabled=True)
                jd_text = new_jd.strip()
        else:
            cv_text = st.session_state.cv_text
            jd_text = st.session_state.jd_text
    else:
        cv_text = st.session_state.cv_text
        jd_text = st.session_state.jd_text
    
    # Option to regenerate interview questions
    if st.button("Regenerate Interview Questions", key="regen_questions6"):
        if cv_text and jd_text:
            new_questions = {}  # (Implement regeneration similar to Module 5 if desired)
            st.session_state.parsed_questions = new_questions
            st.rerun()
    
    selected_question_obj = None
    if st.session_state.parsed_questions and isinstance(st.session_state.parsed_questions, dict):
        category = st.selectbox("Select question category", list(st.session_state.parsed_questions.keys()), key="practice_category")
        qlist = st.session_state.parsed_questions.get(category, [])
        if qlist:
            question_options = { f"{i+1}. {format_question(q)[0]}": q for i, q in enumerate(qlist) }
            selected_key = st.selectbox("Select a question to answer", list(question_options.keys()), key="practice_question")
            selected_question_obj = question_options[selected_key]
        else:
            st.warning("No questions available in this category.")
    else:
        st.warning("No interview questions found. Please generate questions first.")
    
    custom_question = st.text_input("Or enter a custom question to practice (optional):", key="custom_question")
    if custom_question.strip():
        selected_question_obj = {
            "question": custom_question.strip(),
            "guidelines": "N/A",
            "fit_score": "N/A"
        }
    
    if selected_question_obj:
        summary, details = format_question(selected_question_obj)
        st.markdown(f"**Question:** {summary}")
        with st.expander("Show Question Details"):
            st.markdown(details)
        
        answer_method = st.radio("Choose answer input method:", ["Record Audio", "Type Answer"], key="answer_method")
        if answer_method == "Record Audio":
            audio_value = st.audio_input("Record a voice message", key="audio_recorder")
            if audio_value:
                st.audio(audio_value)
                if st.button("Submit Answer", key="submit_audio"):
                    with st.spinner("Transcribing and generating feedback..."):
                        try:
                            audio_bytes = audio_value.read()
                            audio_file = io.BytesIO(audio_bytes)
                            audio_file.name = "recording.wav"
                            transcript_response = client.audio.transcriptions.create(
                                model="whisper-1",
                                file=audio_file
                            )
                            transcript = transcript_response.text.strip()
                            if transcript and len(transcript.split()) >= 5:
                                st.markdown("**Transcribed Answer:**")
                                st.write(transcript)
                                feedback_prompt = FEEDBACK_PROMPT.format(question=summary, answer=transcript)
                                feedback_response = client.chat.completions.create(
                                    model="gpt-4o",
                                    messages=[
                                        {"role": "system", "content": "You are a helpful interview coach."},
                                        {"role": "user", "content": feedback_prompt}
                                    ],
                                    temperature=0.7,
                                    max_tokens=500
                                )
                                feedback = feedback_response.choices[0].message.content
                                st.markdown("**Feedback on Your Answer:**")
                                st.markdown(feedback, unsafe_allow_html=True)
                            else:
                                st.error("Your transcribed answer is too short.")
                        except Exception as e:
                            st.error(f"Error during transcription/feedback: {e}")
        else:
            typed_answer = st.text_area("Type your answer here:", key="typed_answer")
            if st.button("Submit Answer", key="submit_answer_typed"):
                if typed_answer and len(typed_answer.split()) >= 5:
                    st.markdown("**Your Answer:**")
                    st.write(typed_answer)
                    with st.spinner("Generating feedback..."):
                        try:
                            feedback_prompt = FEEDBACK_PROMPT.format(question=summary, answer=typed_answer)
                            feedback_response = client.chat.completions.create(
                                model="gpt-4o",
                                messages=[
                                    {"role": "system", "content": "You are a helpful interview coach."},
                                    {"role": "user", "content": feedback_prompt}
                                ],
                                temperature=0.7,
                                max_tokens=500
                            )
                            feedback = feedback_response.choices[0].message.content
                            st.markdown("**Feedback on Your Answer:**")
                            st.markdown(feedback, unsafe_allow_html=True)
                        except Exception as e:
                            st.error(f"Error generating feedback: {e}")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Back", key="back_practice"):
            go_to_module(5)
    with col3:
        if st.button("Start Again", key="restart_practice"):
            reset_state()

