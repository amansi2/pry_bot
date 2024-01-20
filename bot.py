import os
import logging
import mysql.connector
import time
from slack import WebClient
from pathlib import Path
from dotenv import load_dotenv
from slackeventsapi import SlackEventAdapter
from flask import Flask, json, request, send_from_directory
from flask_ngrok import run_with_ngrok

env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)


app = Flask(__name__)
run_with_ngrok(app)


#slack initation
slack_token = os.environ['SLACK_TOKEN']
slack_client = WebClient(token=slack_token)
slack_events_adapter = SlackEventAdapter(os.environ['SIGNING_SECRET'], '/slack/events', app)

# Global variable to store the last timestamp of an API call
last_api_call_timestamp = 0

# Function to introduce rate limiting
def rate_limit():
    global last_api_call_timestamp
    # Add a delay only if the previous API call was made within the last second
    current_timestamp = time.time()
    if current_timestamp - last_api_call_timestamp < 1:
        time.sleep(1)
    last_api_call_timestamp = time.time()


#logging
logging.basicConfig(level=logging.INFO)
logger=logging.getLogger("logging.INFO")


# MySQL database setup
db_config = {
    'host': os.environ['MYSQL_HOST'],
    'user': os.environ['MYSQL_USER'],
    'password': os.environ['MYSQL_PASSWORD'],
    'database': os.environ['MYSQL_DATABASE'],
}

try:
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()

    # Create the user_status table if not exists
    cursor.execute('''
       CREATE TABLE IF NOT EXISTS user_status (
          user_id VARCHAR(255) PRIMARY KEY,
          status TEXT
);

    ''')

    conn.commit()
    logger.info("MySQL database connection established successfully.")
except mysql.connector.Error as e:
    logger.error(f"Error connecting to MySQL database: {e}")
# Keep track of users who have received a message
sent_users = set()


#send mention message function
def send_mention_messages(cursor, slack_client, event_data):
    channel_id = event_data['event']['channel']
    mentioning_user_id = event_data['event']['user']
    bot_user_id = slack_client.auth_test()['user_id']

    # Get the list of all users in the workspace
    users = slack_client.users_list()['members']

    for user in users:
        user_id = user['id']
        # Check if mentioning_user_id and bot_user_id are not None
        if mentioning_user_id and bot_user_id:
        # Skip mentioning user and bot user
         if user_id != mentioning_user_id and user_id != bot_user_id:
            # Construct a personalized message for each user
            message = f"Hey <@{user_id}>, please update your daily status!"

            # Send the personalized message to the user
            rate_limit()
            slack_client.chat_postMessage(
                channel=user_id,
                text=message,
                 attachments=[
                        {
                            "text": "Update your daily status:",
                            "fallback": "You are unable to update your daily status.",
                            "callback_id": f"update_status_{user_id}",
                            "color": "#3AA3E3",
                            "attachment_type": "default",
                            "actions": [
                                {
                                    "name": "status",
                                    "text": "Update status",
                                    "type": "button",
                                    "value": "in_progress"
                                }
                            ]
                        }
                    ]
            )

            try:
                with conn.cursor() as cursor:
                 cursor.execute('''
                  INSERT INTO user_status (user_id, status) VALUES (%s, %s)
                  ON DUPLICATE KEY UPDATE status=%s;
                  ''', (user_id, message, message))

                conn.commit()
                logger.info(f"Data stored in the database for user {user_id}")
            except mysql.connector.Error as e:
                logger.error(f"Error storing data in the database: {e}")
              # Add the user to the set of sent users
                sent_users.add(user_id)
            except mysql.connector.Error as e:
             logger.error(f"Error storing data in the database: {e}")
#slack event handler
@slack_events_adapter.on("app_mention")
def handle_mention(event_data):
    # Clear the set of sent users for each new mention event
    sent_users.clear()

    try:
        send_mention_messages(cursor, slack_client, event_data)
        logger.info("Mention messages sent successfully.")
    except Exception as e:
        logger.error(f"Error handling mention event: {e}")

#favicon
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')


      


# Slack interactive component listener
@app.route("/interactive", methods=["POST"])
def interactive():
    try:
        payload = request.get_json()
        logger.info(f"Received interactive payload: {payload}")
        user_id = payload['user']['id']
        callback_id = payload['callback_id']
        
        if callback_id.startswith('update_status'):
         # This is a response to the "update daily status" message
           user_response = payload['actions'][0]['value']   
        # Store the user responses in the database
           with conn.cursor() as cursor:
            cursor.execute('''
               INSERT INTO user_responses (user_id, response) VALUES (%s, %s)
               ON DUPLICATE KEY UPDATE response = %s;
            ''', (user_id, user_response, user_response))
           conn.commit()
           logger.info("User response stored in the database")

        # Send a confirmation message to the user
           rate_limit()
           slack_client.chat_postMessage(
            channel=user_id,
            text=f"Thanks for your response: {user_response}"
        )
 # Print the entire Slack API response
        api_response = slack_client.api_call("chat.postMessage", channel=user_id, text=f"Thanks for your response: {user_response}")
        logger.info(f"Slack API response: {api_response}")

    except Exception as e:
        app.logger.error(f"Error processing interactive event: {e}")
        return "Error processing interactive event", 500

    return ""



# Route for the root URL ("/")
@app.route('/', methods=['GET'])
def index():
    return 'Welcome to your Slack Bot!'



# Slack Events API endpoint
@app.route('/slack/events', methods=['POST'])
def slack_events():
    # Verify the request is coming from Slack using the verification token
    if request.form['token'] == slack_token:
        data = json.loads(request.data)

        # Check if the event type is 'url_verification'
        if 'type' in data and data['type'] == 'url_verification':
            return data['challenge']

        # Process other event types as needed
        if 'event' in data:
            event_type = data['event']['type']

            # Handle different event types
            if event_type == 'message':
                # Extract information from the message event
                user_id = data['event']['user']
                channel_id = data['event']['channel']
                text = data['event']['text']

                # Example: Store message event in the database
                cursor.execute('''
                INSERT INTO user_messages (user_id, channel_id, text) VALUES (%s, %s, %s);
                ''', (user_id, channel_id, text))
                conn.commit()
                app.logger.info("Message event data stored in the database")
        return '', 200
    else:
        return 'Invalid request', 403

if __name__ == "__main__":
    app.run()
