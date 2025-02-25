import streamlit as st
import os
import json
import requests
from bs4 import BeautifulSoup
import PyPDF2
import io
import markdown as md  # alias for markdown
from datetime import datetime, timedelta
from openai import OpenAI
import firebase_admin
from firebase_admin import credentials, auth, db
import stripe
import tempfile

# -------------------------------
# Load configuration from st.secrets
# -------------------------------
config = st.secrets
APP_URL = config.get("APP_URL", "http://localhost:8501/")

# -------------------------------
# Load API Keys & Credentials from st.secrets
# -------------------------------
openai_api_key = config.get("OPENAI_API_KEY")
app_users_json = config.get("APP_USERS")
if not openai_api_key:
    st.error("OPENAI_API_KEY not found in st.secrets.")
    st.stop()

# Stripe Credentials
STRIPE_API_KEY = config.get("STRIPE_API_KEY")
PRO_PRICE_ID = config.get("PRO_PRICE_ID")
ULTIMATE_PRICE_ID = config.get("ULTIMATE_PRICE_ID")
if not STRIPE_API_KEY or not PRO_PRICE_ID or not ULTIMATE_PRICE_ID:
    st.error("Stripe credentials are missing in st.secrets.")
    st.stop()

# -------------------------------
# Helper: Reset usage for all modules
# -------------------------------
def reset_usage():
    return {
        "Module 1": 0,
        "Module 2": 0,
        "Module 3": 0,
        "Module 4": 0,
        "Module 5": 0,
        "Module 6": 0,
    }

# -------------------------------
# Helper: Update subscription using Stripe session data
# -------------------------------
SIX_MONTHS = timedelta(days=180)
def update_tier_by_checkout_session(session_id):
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        purchase_plan = session.metadata.get("purchase_plan", "free")
        customer_email = session.customer_email
        # Look up the user in Firebase by matching the email.
        users_ref = db.reference("users")
        users = users_ref.get() or {}
        for uid, user in users.items():
            if user.get("email", "").lower() == customer_email.lower():
                current_plan = user.get("subscription", {}).get("package", "free")
                if purchase_plan == "pro" and current_plan == "ultimate":
                    st.warning("You are currently on the Ultimate plan. Switching to the Pro package will cause you to lose access to Ultimate advantages.")
                    if st.button("Confirm Downgrade", key="confirm_downgrade_checkout"):
                        expiry_date = datetime.now() + SIX_MONTHS
                        usage_init = reset_usage()
                        users_ref.child(uid).update({
                            "subscription": {
                                "package": purchase_plan,
                                "expiry": expiry_date.isoformat()
                            },
                            "usage": usage_init
                        })
                        return True, purchase_plan, customer_email
                    else:
                        return False, None, customer_email
                else:
                    expiry_date = datetime.now() + SIX_MONTHS
                    usage_init = reset_usage()
                    users_ref.child(uid).update({
                        "subscription": {
                            "package": purchase_plan,
                            "expiry": expiry_date.isoformat()
                        },
                        "usage": usage_init
                    })
                    return True, purchase_plan, customer_email
        return False, None, customer_email
    except Exception as e:
        st.error(f"Error updating subscription via checkout session: {e}")
        return False, None, None

# -------------------------------
# Check Query Parameters for Payment Status using st.query_params
# -------------------------------
if "status" in st.query_params:
    status = st.query_params["status"]
    if status == "success":
        st.success("Payment successful!")
        session_id = st.query_params.get("session_id")
        if session_id:
            updated, plan, customer_email = update_tier_by_checkout_session(session_id)
            if updated:
                st.success(f"Subscription upgraded to {plan.capitalize()} for {customer_email}! All usage counters have been reset.")
            else:
                st.info("Payment completed. Please log in with the same email to see your upgraded subscription.")
        st.query_params.clear()  # Clear query parameters from the URL
        st.rerun()
    elif status == "cancel":
        st.warning("Payment failed or was cancelled.")
        st.session_state.step = 0
        st.session_state.page = "landing"
        st.query_params.clear()
        st.rerun()

# -------------------------------
# Define checkout session creation function (with metadata)
# -------------------------------
stripe.api_key = STRIPE_API_KEY
def create_checkout_session(price_id, customer_email, purchase_plan):
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            mode='payment',
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            customer_email=customer_email,
            success_url=f"{APP_URL}?status=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{APP_URL}?status=cancel",
            metadata={"purchase_plan": purchase_plan}
        )
        return session.url
    except Exception as e:
        st.error(f"Error creating checkout session: {e}")
        return None

# -------------------------------
# Firebase Client Configuration
# -------------------------------
firebase_config = config.get("FIREBASE", {})
firebase_client_config = {
    "apiKey": firebase_config.get("API_KEY"),
    "authDomain": firebase_config.get("AUTH_DOMAIN"),
    "databaseURL": firebase_config.get("DATABASE_URL"),
    "projectId": firebase_config.get("PROJECT_ID"),
    "storageBucket": firebase_config.get("STORAGE_BUCKET"),
    "messagingSenderId": firebase_config.get("MESSAGING_SENDER_ID"),
    "appId": firebase_config.get("APP_ID")
}
if not firebase_client_config.get("apiKey"):
    st.error("Firebase configuration is missing in st.secrets.")
    st.stop()

# -------------------------------
# Firebase Admin SDK Initialization
# -------------------------------
firebase_admin_creds = config.get("FIREBASE_ADMIN_CREDENTIALS")
if not firebase_admin_creds:
    st.error("FIREBASE_ADMIN_CREDENTIALS not set in st.secrets.")
    st.stop()

firebase_admin_creds = dict(firebase_admin_creds)
if "\\n" in firebase_admin_creds.get("private_key", ""):
    firebase_admin_creds["private_key"] = firebase_admin_creds["private_key"].replace("\\n", "\n")

try:
    with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".json") as temp_file:
        json.dump(firebase_admin_creds, temp_file)
        temp_file_path = temp_file.name
except Exception as e:
    st.error(f"Invalid Firebase Admin Credentials: {str(e)}")
    st.stop()

admin_cred = credentials.Certificate(firebase_admin_creds)
try:
    firebase_admin.get_app()
except ValueError:
    firebase_admin.initialize_app(admin_cred, {
        "databaseURL": firebase_client_config.get("databaseURL")
    })

# -------------------------------
# Firebase Auth Functions (using REST API & Admin SDK)
# -------------------------------
FIREBASE_REST_API = "https://identitytoolkit.googleapis.com/v1"

def login_user(email, password):
    api_key = firebase_config.get("API_KEY")
    url = f"{FIREBASE_REST_API}/accounts:signInWithPassword?key={api_key}"
    payload = {
        "email": email,
        "password": password,
        "returnSecureToken": True
    }
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        data = response.json()
        try:
            decoded = auth.verify_id_token(data["idToken"])
        except Exception as e:
            st.error("Token verification failed.")
            return None
        return data
    else:
        error_msg = response.json().get("error", {}).get("message", "Unknown error")
        st.error("Login failed: " + error_msg)
        return None

def signup_user(email, password):
    api_key = firebase_config.get("API_KEY")
    url = f"{FIREBASE_REST_API}/accounts:signUp?key={api_key}"
    payload = {
        "email": email,
        "password": password,
        "returnSecureToken": True
    }
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        data = response.json()
        try:
            link = auth.generate_email_verification_link(email)
            st.info(f"Please verify your email using this link: {link}")
        except Exception as e:
            st.error("Failed to generate email verification link: " + str(e))
        store_user_in_db(data, email)
        return data
    else:
        error_msg = response.json().get("error", {}).get("message", "Unknown error")
        st.error("Sign up failed: " + error_msg)
        return None

def logout_user():
    st.session_state.user = None
    st.session_state.customer_email = ""
    st.session_state.auth_page = "login"
    st.session_state.page = "landing"
    st.rerun()

# -------------------------------
# Store New User Info in Realtime Database
# -------------------------------
def store_user_in_db(user, email):
    ref = db.reference("users")
    usage_init = reset_usage()
    subscription_init = {"package": "free", "expiry": None}
    ref.child(user["localId"]).set({
         "email": email,
         "created_at": datetime.now().isoformat(),
         "usage": usage_init,
         "subscription": subscription_init
    })

# -------------------------------
# Firebase Admin Helper Functions for Usage & Subscription
# -------------------------------
def get_user_data():
    user_id = st.session_state.user["localId"]
    ref = db.reference("users").child(user_id)
    return ref.get()

def record_module_run(module_name):
    user_id = st.session_state.user["localId"]
    user_ref = db.reference("users").child(user_id).child("usage")
    current_usage = user_ref.child(module_name).get() or 0
    new_usage = current_usage + 1
    user_ref.update({module_name: new_usage})

def can_run_module(module_name):
    user_data = get_user_data()
    subscription = user_data.get("subscription", {"package": "free", "expiry": None})
    package = subscription.get("package", "free")
    expiry = subscription.get("expiry")
    if expiry:
        expiry_date = datetime.fromisoformat(expiry)
        if datetime.now() > expiry_date:
            user_id = st.session_state.user["localId"]
            db.reference("users").child(user_id).child("subscription").update({"package": "free", "expiry": None})
            package = "free"
    usage = user_data.get("usage", {})
    current_runs = usage.get(module_name, 0)
    if package == "free":
        return current_runs < 5
    elif package == "pro":
        return current_runs < 100
    elif package == "ultimate":
        return True
    return False

def get_left_runs(module_name):
    user_data = get_user_data()
    subscription = user_data.get("subscription", {"package": "free", "expiry": None})
    package = subscription.get("package", "free")
    used = user_data.get("usage", {}).get(module_name, 0)
    if package == "free":
        return max(5 - used, 0)
    elif package == "pro":
        return max(100 - used, 0)
    elif package == "ultimate":
        return "Unlimited"
    return 0

def update_tier_after_payment(plan):
    # When a logged-in user upgrades, reset all usage counters
    expiry_date = datetime.now() + SIX_MONTHS
    user_id = st.session_state.user["localId"]
    usage_init = reset_usage()
    db.reference("users").child(user_id).update({
         "subscription": {
             "package": plan,
             "expiry": expiry_date.isoformat()
         },
         "usage": usage_init
    })
    st.success(f"Successfully upgraded to {plan.capitalize()}! All module usage has been reset and access is valid until {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}")

# -------------------------------
# Utility Functions (PDF extraction, scraping, etc.)
# -------------------------------
def extract_text_from_pdf(file) -> str:
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
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
        file.seek(0)
        images = convert_from_bytes(file.read())
        ocr_text = ""
        for image in images:
            ocr_text += pytesseract.image_to_string(image) + "\n"
        return ocr_text.strip()
    except Exception as ocr_e:
        st.error(f"Error during OCR: {ocr_e}")
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

def render_card(content: str):
    html_content = md.markdown(content)
    st.markdown(f"<div class='card'>{html_content}</div>", unsafe_allow_html=True)

def clean_markdown_output(text: str) -> str:
    return text.replace("```markdown", "").replace("```", "")

def format_question(q_obj: dict):
    summary = q_obj.get("question", "").strip()
    guidelines = q_obj.get("guidelines", "").strip()
    fit_score = q_obj.get("fit_score", "")
    details = f"**Guidelines:** {guidelines}\n\n**Candidate Fit Score:** {fit_score}"
    return summary, details

def update_or_keep_cv_jd():
    st.info("You can keep your current CV/JD or update them below.")
    st.markdown("#### Current CV (Read-only)")
    st.text_area("CV Text", value=st.session_state.cv_text or "No CV provided yet.", height=120, disabled=True)
    st.markdown("#### Current JD (Read-only)")
    st.text_area("JD Text", value=st.session_state.jd_text or "No JD provided yet.", height=120, disabled=True)
    choice = st.radio("Do you want to update anything?",
                      ["Keep both CV & JD", "Update CV only", "Update JD only", "Update both"],
                      key=f"update_choice_module_{st.session_state.get('step', 0)}")
    if choice == "Update CV only":
        new_cv = st.file_uploader("Upload your new CV (PDF)", type=["pdf"], key=f"cv_reupload_m{st.session_state.get('step', 0)}")
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
    elif choice == "Update both":
        st.write("### Update CV")
        new_cv = st.file_uploader("Upload your new CV (PDF)", type=["pdf"], key=f"cv_reupload_both_m{st.session_state.get('step', 0)}")
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
        new_jd_manual = st.text_area("Paste new Job Description", height=120, key=f"jd_textarea_both_m{st.session_state.get('step', 0)}")
        if new_jd_manual.strip():
            if st.button("Use This New JD (Both)"):
                st.session_state.jd_text = new_jd_manual.strip()
                st.success("Job description updated successfully!")
    else:
        pass

# -------------------------------
# Helper: Generate Interview Questions
# -------------------------------
def generate_interview_questions(cv_text, jd_text):
    with st.spinner("Generating interview questions..."):
        try:
            prompt = INTERVIEW_QUESTIONS_PROMPT.format(cv_text=cv_text, jd_text=jd_text, language=st.session_state.language)
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
            if hasattr(message, "function_call") and message.function_call:
                arguments = message.function_call.arguments
            else:
                arguments = message.content
            arguments_clean = arguments.replace("\n", "\\n")
            parsed = json.loads(arguments_clean)
            st.session_state.parsed_questions = parsed
            st.session_state.interview_output = json.dumps(parsed, indent=2)
            st.success("Interview questions generated successfully!")
        except Exception as e:
            st.error(f"Error generating interview questions: {e}")

# -------------------------------
# Prompt Templates
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
# Initialize OpenAI Client
# -------------------------------
client = OpenAI(api_key=openai_api_key)

# -------------------------------
# Helper Functions for Upgrade and Module Instructions
# -------------------------------
def buy_package_button(label, price_id, purchase_plan):
    if st.session_state.customer_email:
        # Get current subscription package
        user_data = get_user_data()
        current_plan = user_data.get("subscription", {}).get("package", "free")
        
        # If user is on Ultimate and trying to buy Pro, show a warning and confirmation button.
        if purchase_plan == "pro" and current_plan == "ultimate":
            st.warning("You are currently on the Ultimate plan. Switching to the Pro package will cause you to lose access to Ultimate advantages.")
            if st.button(f"Confirm Downgrade to {label}", key=f"confirm_{purchase_plan}"):
                checkout_url = create_checkout_session(price_id, st.session_state.customer_email, purchase_plan)
                if checkout_url:
                    st.markdown(f'<script>window.open("{checkout_url}", "_blank");</script>', unsafe_allow_html=True)
                    st.markdown(f"[Click here if not redirected automatically]({checkout_url})", unsafe_allow_html=True)
        else:
            if st.button(f"Buy {label}", key=f"buy_{purchase_plan}"):
                checkout_url = create_checkout_session(price_id, st.session_state.customer_email, purchase_plan)
                if checkout_url:
                    st.markdown(f'<script>window.open("{checkout_url}", "_blank");</script>', unsafe_allow_html=True)
                    st.markdown(f"[Click here if not redirected automatically]({checkout_url})", unsafe_allow_html=True)
    else:
        st.error("Please enter your email for upgrade.")

def show_module_instructions(module_title, instructions):
    with st.expander("Module Instructions"):
        st.markdown(f"#### {module_title}")
        st.markdown(instructions)

# -------------------------------
# Global CSS and Page Configuration
# -------------------------------
st.set_page_config(page_title="Career Catalyst", layout="wide")
st.markdown(
    """
    <style>
    body { background-color: #f7f7f7; font-family: 'Segoe UI', sans-serif; color: #2c3e50; }
    .main, .stApp { background: #ffffff; border-radius: 10px; padding: 30px 20px; }
    header, footer { visibility: hidden; }
    h1, h2, h3 { text-align: center; margin-top: 0; color: #2c3e50; }
    .module-title { color: #2c3e50; border-bottom: 2px solid #d4af37; margin-bottom: 10px; padding-bottom: 5px; text-transform: uppercase; text-align: center; }
    .card { background-color: #ffffff; border: 2px solid #d4af37; border-radius: 10px; padding: 20px; margin-bottom: 20px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
    .upgrade-box { background-color: #e7f0fd; border: 2px solid #4a90e2; border-radius: 10px; padding: 20px; margin: 10px; }
    </style>
    """,
    unsafe_allow_html=True
)

# -------------------------------
# Session State Initialization for Main App
# -------------------------------
if "user" not in st.session_state:
    st.session_state.user = None
if "customer_email" not in st.session_state:
    st.session_state.customer_email = ""
if "auth_page" not in st.session_state:
    st.session_state.auth_page = "login"
if "step" not in st.session_state:
    st.session_state.step = 0  # 0 = landing, 1..6 = modules
if "page" not in st.session_state:
    st.session_state.page = "landing"
if "cv_text" not in st.session_state: st.session_state.cv_text = None
if "cv_analysis" not in st.session_state: st.session_state.cv_analysis = None
if "jd_text" not in st.session_state: st.session_state.jd_text = None
if "jd_analysis" not in st.session_state: st.session_state.jd_analysis = None
if "fit_analysis" not in st.session_state: st.session_state.fit_analysis = None
if "cv_improvement" not in st.session_state: st.session_state.cv_improvement = None
if "interview_output" not in st.session_state: st.session_state.interview_output = None
if "parsed_questions" not in st.session_state: st.session_state.parsed_questions = None
if "language" not in st.session_state: st.session_state.language = "English"
if "show_upgrade" not in st.session_state:
    st.session_state.show_upgrade = False

# -------------------------------
# Sidebar Navigation with Logout, Settings, and Start Button
# -------------------------------
with st.sidebar:
    # Show Start button only if the user hasn't started any module yet
    if st.session_state.step == 0:
        if st.button("Start"):
            st.session_state.step = 1
            st.rerun()
    st.button("Logout", on_click=logout_user)
    if st.button("Settings"):
        st.session_state.page = "settings"
        st.rerun()
    # Existing module navigation if the user has started a module
    if st.session_state.step > 0:
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
                st.session_state.page = "landing"
                st.rerun()
    st.markdown("---")
    if st.button("Legal Mentions", key="legal_mentions"):
        st.session_state.page = "legal"
        st.rerun()
    if st.button("Contact Us", key="contact_us"):
        st.session_state.page = "contact"
        st.rerun()
    if st.button("About Us", key="about_us"):
        st.session_state.page = "about"
        st.rerun()

# -------------------------------
# Legal Mentions Page
# -------------------------------
if st.session_state.page == "legal":
    st.title("Legal Mentions")
    st.markdown("""
**Legal Mentions**  
This service is provided by Career Catalyst and is subject to French law. All information provided on this platform is for informational purposes only and does not constitute legal advice. Please consult a legal professional for advice tailored to your situation.  
*© Career Catalyst. All rights reserved.*
    """)
    if st.button("Back to Landing"):
         st.session_state.page = "landing"
         st.rerun()
    st.stop()

# -------------------------------
# Contact Us Page
# -------------------------------
if st.session_state.page == "contact":
    st.title("Contact Us")
    st.markdown("For any inquiries or support, please email: [careercatalysthelpdesk@gmail.com](mailto:careercatalysthelpdesk@gmail.com)")
    if st.button("Back to Landing"):
         st.session_state.page = "landing"
         st.rerun()
    st.stop()

# -------------------------------
# About Us Page
# -------------------------------
if st.session_state.page == "about":
    st.title("About Us")
    st.markdown("""
Career Catalyst is a comprehensive career enhancement platform designed to help job seekers optimize their CVs, understand job descriptions, assess their fit for roles, and prepare for interviews.

**Our Capabilities:**
- **CV Analysis:** Detailed insights on your CV, highlighting strengths and areas for improvement.
- **Job Analysis:** In-depth breakdown of job descriptions to help you tailor your applications.
- **Fit Analysis:** An evaluation of how well your profile matches job requirements.
- **CV Improvement Suggestions:** Actionable recommendations to optimize your CV.
- **Interview Preparation:** Custom interview questions and real-time feedback to boost your interview performance.

For more information or support, please contact us at [careercatalysthelpdesk@gmail.com](mailto:careercatalysthelpdesk@gmail.com).
    """)
    if st.button("Back to Landing"):
         st.session_state.page = "landing"
         st.rerun()
    st.stop()

# -------------------------------
# Settings Page with Upgrade Options
# -------------------------------
if st.session_state.page == "settings":
    st.title("Settings")
    if st.button("← Back to Landing"):
        st.session_state.page = "landing"
        st.rerun()
    user_data = get_user_data()
    subscription = user_data.get("subscription", {"package": "free", "expiry": None})
    package = subscription.get("package", "free")
    expiry = subscription.get("expiry")
    st.subheader("Your Package Information")
    st.write("**Current Package:**", package.capitalize())
    if expiry:
        expiry_date = datetime.fromisoformat(expiry)
        st.write("**Access Valid Until:**", expiry_date.strftime('%Y-%m-%d %H:%M:%S'))
    else:
        st.write("**Access Valid Until:** Free access (no expiry)")
    st.markdown("### Module Usage Summary")
    usage = user_data.get("usage", {})
    for module, runs in usage.items():
        left = get_left_runs(module)
        st.write(f"**{module}:** {runs} runs used, **Remaining:** {left}")
    st.markdown("---")
    st.markdown("## Upgrade Your Access (6-Month Access)")
    st.markdown("Upgrade to **Pro** (100 runs per module) or **Ultimate** (Unlimited runs) with a one‑time payment.")
    if not st.session_state.customer_email:
        st.session_state.customer_email = st.text_input("Enter your email for upgrade", key="global_email")
    else:
        st.text_input("Your email for upgrade", value=st.session_state.customer_email, key="global_email", disabled=True)
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("<div class='upgrade-box'>", unsafe_allow_html=True)
        st.markdown("### Pro Package")
        st.markdown("**Price:** $11.99 (One-time)")
        st.markdown("**Benefit:** 100 runs per module for 6 months")
        buy_package_button("Pro Package", PRO_PRICE_ID, "pro")
        st.markdown("</div>", unsafe_allow_html=True)
    with col2:
        st.markdown("<div class='upgrade-box'>", unsafe_allow_html=True)
        st.markdown("### Ultimate Package")
        st.markdown("**Price:** $29.99 (One-time)")
        st.markdown("**Benefit:** Unlimited runs per module for 6 months")
        buy_package_button("Ultimate Package", ULTIMATE_PRICE_ID, "ultimate")
        st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

# -------------------------------
# Landing Page
# -------------------------------
if st.session_state.page == "landing" and st.session_state.step == 0:
    st.title("Career Catalyst")
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

    # Fetch subscription info to conditionally display the free-tier message
    user_data = get_user_data()
    subscription = user_data.get("subscription", {"package": "free", "expiry": None})
    if subscription["package"] == "free":
        st.info("Enjoy our free tier – no payment required to get started!")
    else:
        st.info(f"You are currently on the {subscription['package'].capitalize()} plan. Enjoy your enhanced access!")
        
    if st.button("Get Started"):
        st.session_state.step = 1
        st.rerun()
    if st.button("View Upgrade Options"):
        st.session_state.show_upgrade = True
        st.rerun()
    if st.session_state.show_upgrade:
        st.markdown("## Upgrade Your Access (6-Month Access)")
        st.markdown("Upgrade to **Pro** (100 runs per module) or **Ultimate** (Unlimited runs) with a one‑time payment.")
        if not st.session_state.customer_email:
            st.session_state.customer_email = st.text_input("Enter your email for upgrade", key="global_email")
        else:
            st.text_input("Your email for upgrade", value=st.session_state.customer_email, key="global_email", disabled=True)
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("<div class='upgrade-box'>", unsafe_allow_html=True)
            st.markdown("### Pro Package")
            st.markdown("**Price:** $11.99 (One-time)")
            st.markdown("**Benefit:** 100 runs per module for 6 months")
            buy_package_button("Pro Package", PRO_PRICE_ID, "pro")
            st.markdown("</div>", unsafe_allow_html=True)
        with col2:
            st.markdown("<div class='upgrade-box'>", unsafe_allow_html=True)
            st.markdown("### Ultimate Package")
            st.markdown("**Price:** $29.99 (One-time)")
            st.markdown("**Benefit:** Unlimited runs per module for 6 months")
            buy_package_button("Ultimate Package", ULTIMATE_PRICE_ID, "ultimate")
            st.markdown("</div>", unsafe_allow_html=True)

# -------------------------------
# Module Pages (Modules 1-6)
# -------------------------------
if st.session_state.step == 1:
    show_module_instructions(
        "Module 1: CV Analysis", 
        "Upload your CV in PDF format. This module analyzes your CV to identify your unique strengths and areas for improvement. Use the feedback to optimize your resume for your job search."
    )
    st.title("Module 1: CV Analysis")
    st.markdown("<div class='module-title'>Upload Your CV (PDF) for Analysis</div>", unsafe_allow_html=True)
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
        st.markdown("**Current CV:**")
        st.text_area("CV Text (read-only):", value=st.session_state.cv_text, height=180, disabled=True)
        if st.radio("Do you want to keep or replace this CV?", ("Keep CV", "Replace CV"), key="cv_choice_m1") == "Replace CV":
            new_cv = st.file_uploader("Upload your new CV (PDF)", type=["pdf"], key="cv_upload_replace_m1")
            if new_cv is not None:
                cv_text = extract_text_from_pdf(new_cv)
                if cv_text:
                    st.session_state.cv_text = cv_text
                    st.success("CV replaced successfully!")
    if st.session_state.cv_text:
        st.markdown("### CV Analysis")
        if st.button("Run CV Analysis", key="run_cv_analysis"):
            with st.spinner("Analyzing your CV..."):
                try:
                    prompt = CV_ANALYSIS_PROMPT.format(cv_text=st.session_state.cv_text, language=st.session_state.language)
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.7,
                        max_tokens=1500
                    )
                    result = response.choices[0].message.content.strip()
                    if result and len(result) > 20:
                        st.session_state.cv_analysis = result
                        record_module_run("Module 1")
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

elif st.session_state.step == 2:
    show_module_instructions(
        "Module 2: Job Analysis", 
        "Provide a job description by either scraping a URL or pasting it manually. The module will extract key requirements and challenges from the job posting."
    )
    st.title("Module 2: Job Analysis")
    st.markdown("<div class='module-title'>Provide the Job Description</div>", unsafe_allow_html=True)
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
        st.markdown("**Current Job Description:**")
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
                if st.button("Use This Job Description", key="use_manual_jd_replace_m2"):
                    st.session_state.jd_text = manual_jd.strip()
                    st.success("Job description replaced successfully!")
    if st.session_state.jd_text:
        st.markdown("### Job Description Analysis")
        if st.button("Run Job Analysis", key="run_jd_analysis"):
            with st.spinner("Analyzing job description..."):
                try:
                    prompt = JD_ANALYSIS_PROMPT.format(jd_text=st.session_state.jd_text, language=st.session_state.language)
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.7,
                        max_tokens=1500
                    )
                    result = response.choices[0].message.content.strip()
                    if result and len(result) > 20:
                        st.session_state.jd_analysis = result
                        record_module_run("Module 2")
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

elif st.session_state.step == 3:
    show_module_instructions(
        "Module 3: Fit Analysis & Tips", 
        "This module compares your CV with the job description to generate a fit score along with actionable tips to improve your application."
    )
    st.title("Module 3: Fit Analysis & Tips")
    st.markdown("<div class='module-title'>Let's See How Well Your CV Fits the Job</div>", unsafe_allow_html=True)
    update_or_keep_cv_jd()
    cv_text = st.session_state.cv_text
    jd_text = st.session_state.jd_text
    if cv_text and jd_text:
        st.write("---")
        if st.button("Run Fit Analysis", key="run_fit_analysis"):
            with st.spinner("Calculating fit analysis..."):
                try:
                    prompt = FIT_ANALYSIS_PROMPT.format(cv_text=cv_text, jd_text=jd_text, language=st.session_state.language)
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.7,
                        max_tokens=1500
                    )
                    result = response.choices[0].message.content.strip()
                    if result and len(result) > 20:
                        st.session_state.fit_analysis = result
                        record_module_run("Module 3")
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

elif st.session_state.step == 4:
    show_module_instructions(
        "Module 4: CV Improvement Suggestions", 
        "Receive actionable suggestions to reframe your CV so it better aligns with the job description. You can also choose to update your CV within this module."
    )
    st.title("Module 4: CV Improvement Suggestions")
    st.markdown("<div class='module-title'>Get Actionable Suggestions to Reframe Your CV</div>", unsafe_allow_html=True)
    update_or_keep_cv_jd()
    cv_text = st.session_state.cv_text
    jd_text = st.session_state.jd_text
    if cv_text and jd_text:
        st.write("---")
        if st.button("Generate CV Improvement Suggestions", key="run_cvimp"):
            with st.spinner("Generating suggestions..."):
                try:
                    prompt = CV_ENHANCEMENT_PROMPT.format(cv_text=cv_text, jd_text=jd_text, language=st.session_state.language)
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.7,
                        max_tokens=1000
                    )
                    result = response.choices[0].message.content.strip()
                    if result and len(result) > 20:
                        st.session_state.cv_improvement = result
                        record_module_run("Module 4")
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

elif st.session_state.step == 5:
    show_module_instructions(
        "Module 5: Interview Questions & Guidance", 
        "Generate tailored interview questions based on your CV and job description. The questions are grouped into Technical, Behavioral, and CV Related categories to help you prepare comprehensively."
    )
    st.title("Module 5: Interview Questions & Guidance")
    st.markdown("<div class='module-title'>Generate Interview Questions Based on Your CV & JD</div>", unsafe_allow_html=True)
    update_or_keep_cv_jd()
    cv_text = st.session_state.cv_text
    jd_text = st.session_state.jd_text
    if cv_text and jd_text:
        st.write("---")
        if st.button("Generate Interview Questions", key="gen_int_questions"):
            generate_interview_questions(cv_text, jd_text)
        if st.session_state.interview_output:
            st.markdown("### Interview Questions by Category")
            parsed = st.session_state.parsed_questions
            for category, qlist in parsed.items():
                st.subheader(category)
                questions_formatted = "<br>".join("- " + format_question(q)[0] for q in qlist)
                render_card(questions_formatted)
            record_module_run("Module 5")
    else:
        st.warning("Please ensure both CV and JD are provided before generating questions.")
    if st.session_state.interview_output:
        if st.button("Next: Practice Interview", key="go_module_6"):
            st.session_state.step = 6
            st.rerun()

elif st.session_state.step == 6:
    show_module_instructions(
        "Module 6: Practice Interview", 
        "Practice your interview skills by answering generated or custom questions. Receive real-time feedback on your responses to help you improve."
    )
    st.title("Module 6: Practice Interview")
    st.markdown("<div class='module-title'>Practice Your Interview Skills</div>", unsafe_allow_html=True)
    update_or_keep_cv_jd()
    st.write("---")
    # Regenerate Interview Questions button with warning and module run counting as Module 5
    if st.button("Regenerate Interview Questions", key="regen_questions6"):
        st.warning("Warning: Regenerating interview questions will count as a run of Module 5.")
        if st.session_state.cv_text and st.session_state.jd_text:
            generate_interview_questions(st.session_state.cv_text, st.session_state.jd_text)
            record_module_run("Module 5")
            st.rerun()
        else:
            st.error("Please ensure both CV and JD are provided.")
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
                                feedback_prompt = FEEDBACK_PROMPT.format(question=summary, answer=transcript, language=st.session_state.language)
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
                                record_module_run("Module 6")
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
                            feedback_prompt = FEEDBACK_PROMPT.format(question=summary, answer=typed_answer, language=st.session_state.language)
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
                            record_module_run("Module 6")
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
                if key not in ["user", "auth_page", "customer_email", "page"]:
                    st.session_state.pop(key)
            st.session_state.step = 0
            st.session_state.page = "landing"
            st.rerun()

