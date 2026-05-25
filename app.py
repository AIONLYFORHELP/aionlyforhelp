from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import PyPDF2
import requests
import uuid
import os
import base64

app = Flask(__name__)
CORS(app)

API_KEY = os.environ.get("GROQ_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

pdf_storage = {}

def supabase_get_user(email):
    url = SUPABASE_URL + "/rest/v1/users?email=eq." + email + "&select=*"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": "Bearer " + SUPABASE_KEY
    }
    r = requests.get(url, headers=headers, timeout=10)
    data = r.json()
    return data[0] if data else None

def supabase_create_user(email):
    url = SUPABASE_URL + "/rest/v1/users"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": "Bearer " + SUPABASE_KEY,
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    requests.post(url, headers=headers, json={"email": email, "pdfs_used": 0, "plan": "free_trial"}, timeout=10)

def supabase_update_user(email, pdfs_used):
    url = SUPABASE_URL + "/rest/v1/users?email=eq." + email
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": "Bearer " + SUPABASE_KEY,
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    requests.patch(url, headers=headers, json={"pdfs_used": pdfs_used}, timeout=10)

def extract_text_from_image(image_data, media_type):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": "Bearer " + API_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "model": "llama-4-scout-17b-16e-instruct",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:" + media_type + ";base64," + image_data
                        }
                    },
                    {
                        "type": "text",
                        "text": "Extract ALL text from this image very accurately. Include every word, number, formula you see. Write it clearly."
                    }
                ]
            }
        ],
        "max_tokens": 4000
    }
    r = requests.post(url, headers=headers, json=body, timeout=30)
    result = r.json()
    if "choices" in result:
        return result["choices"][0]["message"]["content"]
    return "Could not extract text from image"

@app.route('/')
def home():
    return send_file('index.html')

@app.route('/tool')
def tool():
    return send_file('tool.html')

@app.route('/get_trial', methods=['POST'])
def get_trial():
    try:
        data = request.json
        email = data.get('email', '').strip().lower()
        if not email:
            return jsonify({'error': 'Please enter email!'})
        user = supabase_get_user(email)
        if not user:
            supabase_create_user(email)
            user = supabase_get_user(email)
        pdfs_used = user['pdfs_used']
        remaining = max(0, 3 - pdfs_used)
        return jsonify({
            'email': email,
            'plan': user['plan'],
            'pdfs_used': pdfs_used,
            'remaining': remaining
        })
    except Exception as e:
        return jsonify({'error': 'Something went wrong. Try again!'})

@app.route('/upload', methods=['POST'])
def upload():
    try:
        email = request.form.get('email', '').strip().lower()
        user = supabase_get_user(email)
        if not user:
            return jsonify({'error': 'Please enter your email first!'})
        pdfs_used = user['pdfs_used']
        plan = user['plan']
        if pdfs_used >= 3 and plan == 'free_trial':
            return jsonify({'error': 'Free trial ended! Please upgrade. Contact: aiforhelp2007@gmail.com'})
        file = request.files['pdf']
        filename = file.filename.lower()
        pdf_text = ""
        pages = 1
        is_image = False
        if filename.endswith('.pdf'):
            reader = PyPDF2.PdfReader(file)
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pdf_text += text
            pages = len(reader.pages)
        elif filename.endswith(('.jpg', '.jpeg', '.png', '.webp')):
            image_data = base64.b64encode(file.read()).decode('utf-8')
            ext = filename.split('.')[-1]
            media_type = 'image/jpeg' if ext in ['jpg', 'jpeg'] else 'image/png'
            pdf_text = extract_text_from_image(image_data, media_type)
            is_image = True
            pages = 1
        else:
            return jsonify({'error': 'Please upload PDF or image file!'})
        session_id = str(uuid.uuid4())
        pdf_storage[session_id] = pdf_text
        new_used = pdfs_used + 1
        supabase_update_user(email, new_used)
        remaining = max(0, 3 - new_used)
        return jsonify({
            'session_id': session_id,
            'pages': pages,
            'remaining': remaining,
            'message': 'File loaded! ' + str(remaining) + ' free uploads remaining.',
            'is_image': is_image
        })
    except Exception as e:
        return jsonify({'error': 'Upload failed. Please try again!'})

@app.route('/ask', methods=['POST'])
def ask():
    try:
        data = request.json
        session_id = data.get('session_id', '')
        question = data.get('question', '')
        language = data.get('language', 'english')
        history = data.get('history', [])
        if session_id not in pdf_storage:
            return jsonify({'answer': 'Please upload a file first!'})
        pdf_text = pdf_storage[session_id]
        lang_instruction = "Answer in Hindi." if language == 'hindi' else "Answer in English."
        chunk_size = 6000
        chunks = [pdf_text[i:i+chunk_size] for i in range(0, min(len(pdf_text), 24000), chunk_size)]
        combined = "\n".join(chunks)
        system_prompt = "You are a helpful AI assistant. " + lang_instruction + " Answer accurately and clearly based on this content:\n" + combined
        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-4:]:
            messages.append({"role": h['role'], "content": h['content']})
        messages.append({"role": "user", "content": question})
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": "Bearer " + API_KEY,
            "Content-Type": "application/json"
        }
        body = {
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.7
        }
        r = requests.post(url, headers=headers, json=body, timeout=30)
        result = r.json()
        if "choices" in result:
            answer = result["choices"][0]["message"]["content"]
        else:
            answer = "Sorry, could not get answer. Please try again!"
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({'answer': 'Request timed out. Please ask again!'})

if __name__ == '__main__':
    print("Starting AIONLYFORHELP...")
    print("Open: http://127.0.0.1:8080")
    app.run(host='0.0.0.0', port=8080, debug=False)