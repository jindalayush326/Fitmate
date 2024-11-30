from flask import Flask, render_template, request, session, flash, redirect, url_for
from openai import OpenAI
from os import getenv
import os
import secrets
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, auth
from PIL import Image
from scripts.helper import process_images, get_gemini_response 
from datetime import datetime
import requests

# Load environment variables
load_dotenv()

# Initialize Flask app
secret_key = secrets.token_hex(16)
app = Flask(__name__)
app.secret_key = secret_key
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
db = SQLAlchemy(app)

# Initialize Firebase Admin SDK
cred = credentials.Certificate("D:/ml/gemini/personality_prediction/aftr-integrated/aftr/aftr/serviceAccountKey.json")  
firebase_admin.initialize_app(cred)

# gets API Key from environment variable OPENAI_API_KEY
client = OpenAI(
  base_url="https://openrouter.ai/api/v1",
  api_key=getenv("OPENROUTER_API_KEY"),
)
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# Fitness API base URL
fitness_api_base_url = "https://fitness10.p.rapidapi.com/"

headers = {
	"X-RapidAPI-Key": "f748f41f52msh8bcf64acbb6c3a6p1a8f7fjsn9697e0c597c1",
	"X-RapidAPI-Host": "fitness10.p.rapidapi.com"
}

# Define the database model
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(50), nullable=False)
    content = db.Column(db.Text, nullable=False)


class User(db.Model):
    __tablename__ = 'usr'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(100), unique=True, nullable=False)
    dob = db.Column(db.Date, nullable=False)    
    system_message = db.Column(db.Text)  # New field for storing the system message
    
# Create the database tables
with app.app_context():
    db.create_all()
    
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('home'))  # Redirect to home page if user is already logged in
    
    if request.method == 'POST':
        if request.form.get('google_signup'):
            # Handle Google signup
            id_token = request.form.get('id_token')
            try:
                # Verify Firebase ID token
                decoded_token = auth.verify_id_token(id_token)
                # Get user details
                name = decoded_token.get("name")
                email = decoded_token.get("email")
                # Save user to the database
                user = User(name=name, email=email)
                db.session.add(user)
                db.session.commit()
                # Set user session
                session['user_id'] = user.id
                session['user_name'] = name
                session['user_email'] = email
                flash("Successfully signed up with Google!")
                return redirect(url_for("home"))
            except auth.AuthError as e:
                flash("Google signup failed: {}".format(e))
                return redirect(url_for("register"))
            
        # Handle regular signup form
        # Get user details from the form
        name = request.form['name']
        username = request.form['username']
        dob_str = request.form['dob']
        dob_date = datetime.strptime(dob_str, '%Y-%m-%d')
        
        # Check if user with same name, username, and dob already exists
        existing_user_same_details = User.query.filter_by(name=name, username=username, dob=dob_date).first()
        if existing_user_same_details:
            # User with same name, username, and dob already exists, redirect to home page
            flash('Welcome back! Redirecting to home page...')
            session['user_id'] = existing_user_same_details.id  # Set user session
            session['user_name'] = existing_user_same_details.name  # Set user name in session
            session['user_username'] = existing_user_same_details.username  # Set username in session
            session['user_dob'] = dob_str  # Set dob in session
            return redirect(url_for('home'))  # Redirect to home page
        
        # Check if username already exists
        existing_user_same_username = User.query.filter_by(username=username).first()
        if existing_user_same_username:
            # Username already exists, show message
            flash('Username already exists. Please choose a different username.')
            return redirect(url_for('register'))  # Redirect to register page again
        
        # Save user to the database
        user = User(name=name, username=username, dob=dob_date)
        db.session.add(user)
        db.session.commit()
        
        flash('Registration successful! Please upload your image.')
        session['user_id'] = user.id  # Set user session
        session['user_name'] = name  # Set user name in session
        session['user_username'] = username  # Set username in session
        session['user_dob'] = dob_str  # Set dob in session
        return redirect(url_for('image'))
    
    # Render the registration form for GET requests
    return render_template('register.html')

# Define the route for the home page
@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect(url_for('register'))  # Redirect to register page if not logged in
    return render_template('home.html')


# Define the route for the image upload page
@app.route('/image', methods=['GET', 'POST'])
def image():
    if 'user_id' not in session:
        return redirect(url_for('register'))  # Redirect to register page if not logged in

    if request.method == 'POST':
        uploaded_files = request.files.getlist('file')
        for uploaded_file in uploaded_files:
            # Process each uploaded file as needed
            pass

        flash('Image uploaded successfully!')
        return redirect(url_for('home'))  # Redirect to home page after image upload

    return render_template('image.html')

# Define the route for uploading images
@app.route('/upload', methods=['POST'])
def upload():
    uploaded_files = request.files.getlist('file')
    image_parts_list = process_images(uploaded_files)
    
    # Retrieve user information from registration
    user_name = session.get('user_name')
    user_username = session.get('user_username')
    user_dob = session.get('user_dob')
    
    # Format user information for the bot's prompt and additional info
    user_info_name = f"Name: {user_name}"
    user_info_for_additional_info = f"Username: {user_username}\nDOB: {user_dob}\n"
    
    # Define the input prompt template for Gemini
    input_prompt_template = f"""
    You are an expert in detecting personality traits and fitness levels. Upon receiving an image of a person, you need to analyze it to classify the individual’s fitness level (beginner, intermediate, or expert) for gym membership suitability, along with other insights such as their interests, hobbies, clothing, and personality traits.

    It is impossible to determine fitness level, BMI, personality traits, interests, or hobbies from a single image. However, based on the person’s appearance, you will make educated guesses, and for further analysis, we ask for the user’s weight and height to calculate BMI and refine the suggestions.

    However, I need more information about the person in the image to provide accurate insights. Could you please provide the following details:

    Name of the person
    Gender (if identifiable)
    Age group
    Additional information related to their fitness journey and interests

    Name: {user_info_name}
    Gender: [Gender]
    Age: [Age]
    Additional Information: {user_info_for_additional_info}
    Fitness Information:

    Fitness Level: [Beginner/Intermediate/Expert based on the analysis of the image and BMI]
    Fitness Goals: [Fitness Goals]
    Current Fitness Level: [Fitness Level]
    Exercise Preferences: [Exercise Preferences]
    Dietary Preferences: [Dietary Preferences]
    Health Conditions/Limitations: [Health Conditions/Limitations]
    """
    
    # Get the Gemini response for the first image
    response_text = get_gemini_response(input_prompt_template, image_parts_list, input_prompt_template)
    
    # Set the system message based on the Gemini response
    print("Terminal Output: ")
    print(f"Bot has received image analysis and determined: {response_text}")
    session['system_message'] = response_text
    flash('System message set successfully!')
    
    # Redirect to the home page
    return render_template('home.html')

# Define the route for sending messages
@app.route('/send_message', methods=['POST'])
def send_message():
    # Check if 'system_message' exists in session
    if 'system_message' not in session:
        flash("System message not set. Please upload an image first.")
        return redirect(url_for('image'))  # Redirect to the image upload page to set the system message

    user_message = request.form['message']
    messages = session.get('conversation', [])
    messages.append({"role": "user", "content": user_message})
    
    # Save the user message to the database
    user_msg = Message(role='user', content=user_message)
    db.session.add(user_msg)
    db.session.commit()
    
    # Handle fitness-related queries
    if "workout" in user_message.lower() or "diet" in user_message.lower() or "fitness" in user_message.lower():
        fitness_response = get_fitness_response(user_message)
        messages.append({"role": "assistant", "content": fitness_response})
        session['conversation'] = messages
        return {'assistant': fitness_response}
    
    # Generate the assistant's response based on the system message and conversation
    response = client.chat.completions.create(
        model="mistralai/mixtral-8x7b-instruct",
        messages=[{"role": "system", "content": session['system_message']}] + messages,
    )
    assistant_message = response.choices[0].message.content
    messages.append({"role": "assistant", "content": assistant_message})
    session['conversation'] = messages
    
    # Save the assistant message to the database
    assistant_msg = Message(role='assistant', content=assistant_message)
    db.session.add(assistant_msg)
    db.session.commit()

    return {'assistant': assistant_message}


def get_fitness_response(user_message):
    # Construct the URL for querying the fitness API
    fitness_api_url = f"{fitness_api_base_url}/query"
    
    # Prepare parameters for the GET request
    params = {'message': user_message}
    
    try:
        # Send the GET request to the fitness API
        response = requests.get(fitness_api_url, params=params, headers=headers)
        fitness_data = response.json()
    
    except requests.exceptions.RequestException as e:
        # Handle any errors that occur during the request
        return "Sorry, there was an error while contacting the fitness API."

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0')
