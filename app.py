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
except Exception:
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
        margin: 0;
        padding: 0;
    }
    .main, .stApp {
        background: #ffffff;
        border-radius: 10px;
        padding: 30px 20px;
    }
    header, footer { visibility: hidden; }
    h1, h2, h3 {
        text-align: center;
        margin-top: 0;
        color: #2c3e50;
    }
    .module-title {
        color: #2c3e50;
        border-bottom: 2px solid #d4af37;
        margin-bottom: 10px;
        padding-bottom: 5px;
        text-transform: uppercase;
        text-align: center;
    }
    .card {
        background-color: #ffffff;
        border: 2px solid #d4af37;
        border-radius: 10px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    .output {
        background: #ecf0f1;
        padding: 15px;
        border-radius: 5px;
        white-space: pre-wrap;
    }
    [data-testid="stSidebar"] {
        right: 0;
        left: auto;
        background: #fafafa;
    }
    .sidebar-content {
        padding: 10px;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# -------------------------------
# Utility Functions
# -------------------------------
def extract_text_from_pdf(file) -> str:
    """Extract text from a PDF using PyPDF2 primarily, fallback to OCR if necessary."""
    try:
        file.seek(0)
        reader = PyPDF2.PdfReader(file)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        if text.strip():
            return text.strip()
    except Exception as e:
        st.error(f"Error processing PDF with PyPDF2: {e}")
    
    # Fallback to OCR using pdf2image and pytesseract
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
        file.seek(0)
        images = convert_from_bytes(file.read())
        ocr_text = ""
        for image in images:
            ocr_text += pytesseract.image_to_string(image) + "\n"
        if ocr_text.strip():
            return ocr_text.strip()
        else:
            return ""
    except Exception as ocr_e:
        st.error(f"Error during OCR: {ocr_e}")
        return ""


def scrape_job_description(url: str):
    """Scrape a job description from a given URL."""
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
        # Fallback: return entire page text
        return soup.get_text(separator="\n", strip=True), None
    except Exception as e:
        return None, str(e)


def render_card(content: str):
    """Render markdown content inside a styled card."""
    html_content = md.markdown(content)
    st.markdown(f"<div class='card'>{html_content}</div>", unsafe_allow_html=True)


def clean_markdown_output(text: str) -> str:
    """Remove markdown code fences from text."""
    return text.replace("```markdown", "").replace("```", "")


def format_question(q_obj: dict):
    """Format question info from the parsed JSON."""
    summary = q_obj.get("question", "").strip()
    guidelines = q_obj.get("guidelines", "").strip()
    fit_score = q_obj.get("fit_score", "")
    details = f"**Guidelines:** {guidelines}\n\n**Candidate Fit Score:** {fit_score}"
    return summary, details

# -------------------------------
# Shared Helper (Modules 3-6):
# Let user keep or update both CV & JD
# -------------------------------
def update_or_keep_cv_jd():
    """For modules 3 and above, let user keep or update BOTH CV and JD."""
    cv_exists = bool(st.session_state.cv_text)
    jd_exists = bool(st.session_state.jd_text)

    st.info("You can keep your current CV/JD or update them below.")

    # Show current CV and JD
    st.markdown("#### Current CV (Read-only)")
    st.text_area("CV Text", value=st.session_state.cv_text or "No CV provided yet.", height=120, disabled=True)

    st.markdown("#### Current JD (Read-only)")
    st.text_area("JD Text", value=st.session_state.jd_text or "No JD provided yet.", height=120, disabled=True)

    choice = st.radio("Do you want to update anything?",
                      ["Keep both CV & JD", "Update CV only", "Update JD only", "Update both"],
                      key=f"update_choice_module_{st.session_state.step}")

    if choice == "Keep both CV & JD":
        pass  # do nothing

    elif choice == "Update CV only":
        new_cv = st.file_uploader("Upload your new CV (PDF)", type=["pdf"], key=f"cv_reupload_m{st.session_state.step}")
        if new_cv:
            cv_text = extract_text_from_pdf(new_cv)
            if cv_text:
                st.session_state.cv_text = cv_text
                st.success("CV updated successfully!")

    elif choice == "Update JD only":
        st.markdown("**Option A:** Scrape from a URL")
        new_jd_url = st.text_input("Enter new JD URL")
        if new_jd_url and st.button("Scrape New JD"):
            with st.spinner("Scraping job posting..."):
                scraped_text, error = scrape_job_description(new_jd_url)
                if error:
                    st.error(f"Scraping error: {error}")
                elif scraped_text:
                    st.session_state.jd_text = scraped_text
                    st.success("Job description updated (scraped) successfully!")

        st.markdown("**Option B:** Paste new JD below")
        new_jd_manual = st.text_area("Paste new Job Description", height=120)
        if new_jd_manual.strip():
            if st.button("Use This New JD"):
                st.session_state.jd_text = new_jd_manual.strip()
                st.success("Job description updated successfully!")

    else:  # Update both
        st.write("### Update CV")
        new_cv = st.file_uploader("Upload your new CV (PDF)", type=["pdf"], key=f"cv_reupload_both_m{st.session_state.step}")
        if new_cv:
            cv_text = extract_text_from_pdf(new_cv)
            if cv_text:
                st.session_state.cv_text = cv_text
                st.success("CV updated successfully!")

        st.write("---")
        st.write("### Update Job Description")
        new_jd_url = st.text_input("Enter new JD URL (optional)")
        if new_jd_url and st.button("Scrape New JD (Both)"):
            with st.spinner("Scraping job posting..."):
                scraped_text, error = scrape_job_description(new_jd_url)
                if error:
                    st.error(f"Scraping error: {error}")
                elif scraped_text:
                    st.session_state.jd_text = scraped_text
                    st.success("Job description updated (scraped) successfully!")

        new_jd_manual = st.text_area("Paste new Job Description", height=120, key=f"jd_textarea_both_m{st.session_state.step}")
        if new_jd_manual.strip():
            if st.button("Use This New JD (Both)"):
                st.session_state.jd_text = new_jd_manual.strip()
                st.success("Job description updated successfully!")




# -------------------------------
# Prompt Templates (with language parameter)
# -------------------------------
CV_ANALYSIS_PROMPT = """Analyze the following CV and provide insightful observations that go beyond simply listing its content. 
Focus on identifying unique strengths and areas for improvement that the candidate may not have realized.

Observations:
- Unique strengths:
- Areas for improvement:

CV Content:
{cv_text}

Respond in {language}."""

JD_ANALYSIS_PROMPT = """Analyze the following job description and organize the key requirements and challenges in markdown. 
Ensure the output is well-formatted with clear bullet points.

Key Requirements:
1. Leadership & Teamwork
2. Technical Requirements
3. Education & Qualifications
4. Soft Skills

Job Description:
{jd_text}

Respond in {language}."""

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

Respond in {language}."""

CV_ENHANCEMENT_PROMPT = """Based solely on the following CV content, identify phrases that can be rephrased to better match 
the provided job description and increase your chances of passing automated screening. For each suggestion, please output in 
the following structured format:
1. **Phrase:** <exact phrase from the CV that should be changed>
   - **New Phrasing:** <the recommended new phrasing (do not add any new skills or fabricate experiences)>
   - **Rationale:** <brief explanation with bullet points on why this change is needed>

If no changes are needed, simply state that the CV is already well-aligned.

CV Content:
{cv_text}

Job Description:
{jd_text}

Respond in {language}."""

INTERVIEW_QUESTIONS_PROMPT = """Based on the following CV and job description, generate interview questions likely to come up 
during an interview for the position grouped by category.
The categories must be exactly: "Technical", "Behavioral", "CV Related".

For each category, output a list of 10 question objects. Each question object must have the following keys:
- "question": A string representing the interview question.
- "guidelines": A string with concise guidance in bullet points (max 100 tokens per question).
- "fit_score": A number representing the candidate's fit score (1-100) with a brief explanation.

Return only a valid JSON object with exactly three keys: "Technical", "Behavioral", and "CV Related". 
Do not include any extra text.

CV:
{cv_text}

Job Description:
{jd_text}

Respond in {language}."""

FEEDBACK_PROMPT = """Evaluate the following interview answer using the appropriate framework (accuracy/soundness, STAR, etc.).
Your response must begin with a clear pass/fail indicator using HTML: if the answer is good, start with '<span style="color: green;">PASS</span>'; 
if not, start with '<span style="color: red;">FAIL</span>'. Then, provide detailed feedback and suggestions for improvement.

Question: {question}
Answer: {answer}

Respond in {language}."""

# -------------------------------
# Session State Initialization
# -------------------------------
if "step" not in st.session_state:
    st.session_state.step = 0  # start at Landing Page

for key in [
    "cv_text", "cv_analysis", "jd_text", "jd_analysis", 
    "fit_analysis", "cv_improvement", "interview_output", 
    "parsed_questions", "jd_scraped"
]:
    if key not in st.session_state:
        st.session_state[key] = None

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "language" not in st.session_state:
    st.session_state.language = "English"  # default language


# -------------------------------
# Sidebar Navigation
# -------------------------------
if st.session_state.step > 0:
    with st.sidebar:
        st.markdown("<div class='sidebar-content'>", unsafe_allow_html=True)
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
                st.session_state.step = mod_num
                st.rerun()

        st.markdown("---")
        if st.button("Reset App", use_container_width=True):
            for key in list(st.session_state.keys()):
                if key not in ["logged_in", "language"]:
                    st.session_state.pop(key)
            st.session_state.step = 0
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)


# -------------------------------
# Landing Page (Module 0)
# -------------------------------
if st.session_state.step == 0:
    st.title("Career Catalyst")
    st.markdown("## Welcome to Career Catalyst")
    st.markdown("""
**Career Catalyst** is your personal career enhancement platform. 
Whether you're applying for a job or preparing for an interview, our tools will help you:
- **Analyze Your CV:** Identify your unique strengths and potential areas for improvement.
- **Break Down Job Descriptions:** Understand key requirements and challenges.
- **Assess Your Fit:** Receive personalized tips and a fit score.
- **Enhance Your CV:** Get actionable suggestions to better tailor your CV.
- **Practice Interviews:** Prepare with targeted interview questions and receive instant feedback.
    """)

    st.markdown("### Select Your Preferred Language")
    language = st.selectbox("Language", options=["English", "French", "Spanish"], key="language_select")
    st.session_state.language = language

    if st.button("Get Started"):
        st.session_state.step = 1
        st.rerun()

# -------------------------------
# Module 1: CV Analysis (CV Only)
# -------------------------------
elif st.session_state.step == 1:
    st.header("Module 1: CV Analysis")
    st.markdown("<div class='module-title'>Upload Your CV (PDF) for Analysis</div>", unsafe_allow_html=True)

    # If CV not in session, force upload
    if not st.session_state.cv_text:
        uploaded_cv = st.file_uploader("Upload your CV (PDF)", type=["pdf"], key="cv_upload_m1")
        if uploaded_cv is not None:
            cv_text = extract_text_from_pdf(uploaded_cv)
            if cv_text:
                st.session_state.cv_text = cv_text
                st.success("CV uploaded successfully!")
        else:
            st.warning("Please upload a valid CV to proceed.")
    else:
        # If CV is in session, let user keep or reupload
        st.markdown("**Current CV**:")
        st.text_area("CV Text (read-only):", value=st.session_state.cv_text, height=180, disabled=True)
        if st.radio("Do you want to keep or replace this CV?", ("Keep CV", "Replace CV"), key="cv_choice_m1") == "Replace CV":
            new_cv = st.file_uploader("Upload your new CV (PDF)", type=["pdf"], key="cv_upload_replace_m1")
            if new_cv is not None:
                cv_text = extract_text_from_pdf(new_cv)
                if cv_text:
                    st.session_state.cv_text = cv_text
                    st.success("CV replaced successfully!")

    # Now show the "Run CV Analysis" if we have a CV
    if st.session_state.cv_text:
        st.markdown("### CV Analysis")
        if st.button("Run CV Analysis", key="run_cv_analysis"):
            with st.spinner("Analyzing your CV..."):
                try:
                    prompt = CV_ANALYSIS_PROMPT.format(
                        cv_text=st.session_state.cv_text,
                        language=st.session_state.language
                    )
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

    if st.session_state.cv_analysis:
        st.markdown("### Analysis Result")
        render_card(st.session_state.cv_analysis)
        if st.button("Next: Job Analysis", key="go_module_2"):
            st.session_state.step = 2
            st.rerun()

# -------------------------------
# Module 2: Job Analysis (JD Only)
# -------------------------------
elif st.session_state.step == 2:
    st.header("Module 2: Job Analysis")
    st.markdown("<div class='module-title'>Provide the Job Description</div>", unsafe_allow_html=True)

    # If JD not in session, ask user to upload or paste
    if not st.session_state.jd_text:
        st.markdown("**Option A:** Scrape from a URL")
        jd_url = st.text_input("Enter Job Posting URL", key="jd_url_m2")
        if jd_url and st.button("Scrape Job Posting", key="scrape_jd_m2"):
            with st.spinner("Scraping job posting..."):
                scraped_text, error = scrape_job_description(jd_url)
                if error:
                    st.error(f"Scraping error: {error}")
                elif scraped_text:
                    st.session_state.jd_text = scraped_text
                    st.success("Job description scraped successfully!")

        st.write("---")
        st.markdown("**Option B:** Paste Manually")
        manual_jd = st.text_area("Paste the Job Description here", key="jd_manual_m2", height=200)
        if manual_jd.strip():
            if st.button("Use This Job Description", key="use_manual_jd_m2"):
                st.session_state.jd_text = manual_jd.strip()
                st.success("Job description set successfully!")
    else:
        # If JD is in session, let user keep or replace
        st.markdown("**Current Job Description**:")
        st.text_area("JD Text (read-only):", value=st.session_state.jd_text, height=180, disabled=True)
        if st.radio("Do you want to keep or replace this JD?", ("Keep JD", "Replace JD"), key="jd_choice_m2") == "Replace JD":
            st.markdown("**Option A:** Scrape from a URL")
            new_jd_url = st.text_input("Enter new JD URL", key="jd_url_replace_m2")
            if new_jd_url and st.button("Scrape New JD", key="scrape_jd_replace_m2"):
                with st.spinner("Scraping job posting..."):
                    scraped_text, error = scrape_job_description(new_jd_url)
                    if error:
                        st.error(f"Scraping error: {error}")
                    elif scraped_text:
                        st.session_state.jd_text = scraped_text
                        st.success("Job description scraped successfully!")

            st.write("---")
            st.markdown("**Option B:** Paste Manually")
            manual_jd = st.text_area("Paste new Job Description here", key="jd_replace_manual_m2", height=200)
            if manual_jd.strip():
                if st.button("Use This New Job Description", key="use_manual_jd_replace_m2"):
                    st.session_state.jd_text = manual_jd.strip()
                    st.success("Job description replaced successfully!")

    # Now show "Run JD Analysis" if we have JD
    if st.session_state.jd_text:
        st.markdown("### Job Description Analysis")
        if st.button("Run Job Analysis", key="run_jd_analysis"):
            with st.spinner("Analyzing job description..."):
                try:
                    prompt = JD_ANALYSIS_PROMPT.format(
                        jd_text=st.session_state.jd_text,
                        language=st.session_state.language
                    )
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.7,
                        max_tokens=1500
                    )
                    result = response.choices[0].message.content.strip()
                    if result and len(result) > 20:
                        st.session_state.jd_analysis = result
                    else:
                        st.error("Job Analysis returned an unexpected result.")
                except Exception as e:
                    st.error(f"Error analyzing job description: {e}")

    if st.session_state.jd_analysis:
        st.markdown("### Job Analysis Result")
        cleaned = clean_markdown_output(st.session_state.jd_analysis)
        render_card(cleaned)
        if st.button("Next: Fit Analysis", key="go_module_3"):
            st.session_state.step = 3
            st.rerun()


# -------------------------------
# Module 3: Fit Analysis & Tips
# -------------------------------
elif st.session_state.step == 3:
    st.header("Module 3: Fit Analysis & Tips")
    st.markdown("<div class='module-title'>Let's See How Well Your CV Fits the Job</div>", unsafe_allow_html=True)

    # Let user update or keep both CV & JD
    update_or_keep_cv_jd()

    cv_text = st.session_state.cv_text
    jd_text = st.session_state.jd_text

    if cv_text and jd_text:
        st.write("---")
        if st.button("Run Fit Analysis", key="run_fit_analysis"):
            with st.spinner("Calculating fit analysis..."):
                try:
                    prompt = FIT_ANALYSIS_PROMPT.format(
                        cv_text=cv_text, 
                        jd_text=jd_text, 
                        language=st.session_state.language
                    )
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
    else:
        st.warning("Please ensure both CV and JD are provided before running Fit Analysis.")

    if st.session_state.get("fit_analysis"):
        st.markdown("### Fit Analysis & Tips")
        render_card(st.session_state.fit_analysis)
        if st.button("Next: CV Improvement Suggestions", key="go_module_4"):
            st.session_state.step = 4
            st.rerun()

# -------------------------------
# Module 4: CV Improvement Suggestions
# -------------------------------
elif st.session_state.step == 4:
    st.header("Module 4: CV Improvement Suggestions")
    st.markdown("<div class='module-title'>Get Actionable Suggestions to Reframe Your CV</div>", unsafe_allow_html=True)

    # Let user update or keep both CV & JD
    update_or_keep_cv_jd()

    cv_text = st.session_state.cv_text
    jd_text = st.session_state.jd_text

    if cv_text and jd_text:
        st.write("---")
        if st.button("Generate CV Improvement Suggestions", key="run_cvimp"):
            with st.spinner("Generating suggestions..."):
                try:
                    prompt = CV_ENHANCEMENT_PROMPT.format(
                        cv_text=cv_text, 
                        jd_text=jd_text, 
                        language=st.session_state.language
                    )
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
    else:
        st.warning("Please ensure both CV and JD are provided before generating suggestions.")

    if st.session_state.cv_improvement:
        st.markdown("### CV Improvement Suggestions")
        render_card(st.session_state.cv_improvement)
        if st.button("Next: Interview Prep", key="go_module_5"):
            st.session_state.step = 5
            st.rerun()

# -------------------------------
# Module 5: Interview Questions & Guidance
# -------------------------------
elif st.session_state.step == 5:
    st.header("Module 5: Interview Questions & Guidance")
    st.markdown("<div class='module-title'>Generate Interview Questions Based on Your CV & JD</div>", unsafe_allow_html=True)

    # Let user update or keep both CV & JD
    update_or_keep_cv_jd()

    cv_text = st.session_state.cv_text
    jd_text = st.session_state.jd_text

    if cv_text and jd_text:
        st.write("---")
        if st.button("Generate Interview Questions", key="gen_int_questions"):
            with st.spinner("Generating interview questions..."):
                try:
                    prompt = INTERVIEW_QUESTIONS_PROMPT.format(
                        cv_text=cv_text, 
                        jd_text=jd_text, 
                        language=st.session_state.language
                    )
                    messages = [{"role": "user", "content": prompt}]
                    functions = [{
                        "name": "get_interview_questions",
                        "description": (
                            "Return interview questions grouped by category in a JSON object. "
                            "Each category's value is a list of question objects with keys 'question', 'guidelines', and 'fit_score'."
                        ),
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
                        # Clean up newlines so we can parse safely
                        arguments_clean = arguments.replace("\n", "\\n")
                        parsed = json.loads(arguments_clean)
                        st.session_state.parsed_questions = parsed
                        st.session_state.interview_output = json.dumps(parsed, indent=2)

                        st.markdown("### Interview Questions by Category")
                        for category, qlist in parsed.items():
                            st.subheader(category)
                            questions_formatted = "<br>".join("- " + format_question(q)[0] for q in qlist)
                            render_card(questions_formatted)

                    except Exception as pe:
                        st.error("Failed to parse interview questions into JSON. Raw output: " + arguments)
                        st.session_state.parsed_questions = {}

                except Exception as e:
                    st.error(f"Error generating interview questions: {e}")
    else:
        st.warning("Please ensure both CV and JD are provided before generating questions.")

    if st.session_state.interview_output:
        if st.button("Next: Practice Interview", key="go_module_6"):
            st.session_state.step = 6
            st.rerun()

# -------------------------------
# Module 6: Practice Interviewing
# -------------------------------
elif st.session_state.step == 6:
    st.header("Module 6: Practice Interview")
    st.markdown("<div class='module-title'>Practice Your Interview Skills</div>", unsafe_allow_html=True)

    # Let user update or keep both CV & JD
    update_or_keep_cv_jd()

    st.write("---")
    if st.button("Regenerate Interview Questions", key="regen_questions6"):
        st.session_state.interview_output = None
        st.session_state.parsed_questions = {}
        st.rerun()

    # Display the stored questions if available
    selected_question_obj = None
    if st.session_state.parsed_questions and isinstance(st.session_state.parsed_questions, dict):
        category = st.selectbox("Select question category", list(st.session_state.parsed_questions.keys()), key="practice_category")
        qlist = st.session_state.parsed_questions.get(category, [])
        if qlist:
            question_options = {f"{i+1}. {format_question(q)[0]}": q for i, q in enumerate(qlist)}
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
        summary, details = selected_question_obj.get("question"), selected_question_obj.get("guidelines")
        st.markdown(f"**Question:** {summary}")
        with st.expander("Show Question Details"):
            st.markdown(details)

        answer_method = st.radio("Choose answer input method:", ["Type Answer", "Record Audio"], key="answer_method")
        if answer_method == "Record Audio":
            audio_value = st.audio_input("Record a voice message", key="audio_recorder")
            if audio_value:
                st.audio(audio_value, format="audio/wav")
                if st.button("Submit Audio Answer", key="submit_audio"):
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
                                feedback_prompt = FEEDBACK_PROMPT.format(
                                    question=summary, 
                                    answer=transcript, 
                                    language=st.session_state.language
                                )
                                feedback_response = client.chat.completions.create(
                                    model="gpt-4o",
                                    messages=[
                                        {"role": "system", "content": "You are a helpful interview coach."},
                                        {"role": "user", "content": feedback_prompt}
                                    ],
                                    temperature=0.7,
                                    max_tokens=2000
                                )
                                feedback = feedback_response.choices[0].message.content
                                st.markdown("**Feedback on Your Answer:**")
                                st.markdown(feedback, unsafe_allow_html=True)
                            else:
                                st.error("Your transcribed answer is too short to evaluate.")
                        except Exception as e:
                            st.error(f"Error during transcription/feedback: {e}")

        else:
            typed_answer = st.text_area("Type your answer here:", key="typed_answer")
            if st.button("Submit Typed Answer", key="submit_answer_typed"):
                if typed_answer and len(typed_answer.split()) >= 5:
                    st.markdown("**Your Answer:**")
                    st.write(typed_answer)
                    with st.spinner("Generating feedback..."):
                        try:
                            feedback_prompt = FEEDBACK_PROMPT.format(
                                question=summary, 
                                answer=typed_answer, 
                                language=st.session_state.language
                            )
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
                else:
                    st.warning("Please type at least a few words to get meaningful feedback.")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Back to Interview Prep", key="back_practice"):
            st.session_state.step = 5
            st.rerun()
    with col3:
        if st.button("Start Over", key="restart_practice"):
            for key in list(st.session_state.keys()):
                if key not in ["logged_in", "language"]:
                    st.session_state.pop(key)
            st.session_state.step = 0
            st.rerun()


