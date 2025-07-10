import os
import re
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, render_template_string
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_mysqldb import MySQL
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import User
from config import Config
import MySQLdb.cursors
import traceback
from datetime import datetime


app = Flask(__name__)
app.config.from_object(Config)

# Initialize extensions
mysql = MySQL(app)
socketio = SocketIO(app, cors_allowed_origins="*")
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Track online users
online_users = {}
user_rooms = {}

@login_manager.user_loader
def load_user(user_id):
    try:
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))
        user = cursor.fetchone()
        if user:
            return User(user['id'], user['username'], user['password'])
        return None
    except Exception as e:
        app.logger.error(f"Error loading user: {e}")
        return None

# Custom Jinja2 filter for time formatting
@app.template_filter('format_time')
def format_time_filter(timestamp):
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
        except:
            return timestamp
    if timestamp:
        return timestamp.strftime('%I:%M %p')  # Format as "11:30 PM"
    return ""

@app.route('/', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('chat'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if not username or not password:
            flash('Please fill in all fields', 'error')
            return render_template('login.html')
        
        try:
            cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
            cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
            user = cursor.fetchone()
            
            if user and check_password_hash(user['password'], password):
                user_obj = User(user['id'], user['username'], user['password'])
                login_user(user_obj)
                return redirect(url_for('chat'))
            else:
                flash('Invalid username or password', 'error')
        except Exception as e:
            app.logger.error(f"Login error: {e}")
            flash('An error occurred. Please try again.', 'error')
    
    return render_template('login.html', hide_navigation=True)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('chat'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if not username or not password:
            flash('Please fill in all fields', 'error')
            return render_template('register.html')
        
        if len(password) < 6:
            flash('Password must be at least 6 characters', 'error')
            return render_template('register.html')
        
        try:
            cursor = mysql.connection.cursor()
            cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
            existing_user = cursor.fetchone()
            
            if existing_user:
                flash('Username already exists', 'error')
            else:
                hashed_password = generate_password_hash(password)
                cursor.execute(
                    'INSERT INTO users (username, password) VALUES (%s, %s)',
                    (username, hashed_password)
                )
                mysql.connection.commit()
                flash('Registration successful! Please login.', 'success')
                return redirect(url_for('login'))
        except Exception as e:
            mysql.connection.rollback()
            app.logger.error(f"Registration error: {e}")
            flash('An error occurred. Please try again.', 'error')
    
    return render_template('register.html', hide_navigation=False)

@app.route('/chat')
@login_required
def chat():
    cursor = None
    try:
        # Get contacts and groups (same as before)
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT username FROM users WHERE username != %s', (current_user.username,))
        contacts = [row['username'] for row in cursor.fetchall()]
        
        cursor.execute('''
            SELECT g.id, g.name 
            FROM `groups` g
            JOIN group_members gm ON g.id = gm.group_id
            WHERE gm.username = %s
        ''', (current_user.username,))
        groups = cursor.fetchall()
        
        # Render template to string
        html = render_template('chat.html', 
                            username=current_user.username,
                            contacts=contacts,
                            groups=groups,
                            hide_navigation=False)
        
        # Remove footer style
        cleaned_html = re.sub(
            r'(<footer\b[^>]*) style="[^"]*"',
            r'\1',
            html,
            count=1
        )
        
        return render_template_string(cleaned_html)
        
    except Exception as e:
        app.logger.error(f"Error fetching chat data: {e}")
        flash('Error loading chat data', 'error')
        return render_template('chat.html', 
                            username=current_user.username,
                            contacts=[],
                            groups=[])
    finally:
        if cursor:
            cursor.close()

@app.route('/groups')
@login_required
def list_groups():
    try:
        # Get all groups
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT * FROM `groups`')
        all_groups = cursor.fetchall()
        
        # Get groups user is in
        cursor.execute('''
            SELECT g.id 
            FROM `groups` g
            JOIN group_members gm ON g.id = gm.group_id
            WHERE gm.username = %s
        ''', (current_user.username,))
        user_groups = [group['id'] for group in cursor.fetchall()]
        
        # Get member counts
        for group in all_groups:
            cursor.execute('''
                SELECT COUNT(*) as count 
                FROM group_members 
                WHERE group_id = %s
            ''', (group['id'],))
            group['member_count'] = cursor.fetchone()['count']
        
        return render_template('groups.html', 
                        all_groups=all_groups,
                        user_groups=user_groups,
                        hide_navigation=False)
        
    except Exception as e:
        app.logger.error(f"Error fetching groups: {e}")
        flash('Error loading groups', 'error')
        return render_template('groups.html', 
                            all_groups=[],
                            user_groups=[])

@app.route('/create-group', methods=['GET', 'POST'])
@login_required
def create_group():
    if request.method == 'POST':
        group_name = request.form.get('group_name', '').strip()
        if not group_name:
            flash('Group name is required', 'error')
            return redirect(url_for('create_group'))
        
        try:
            cursor = mysql.connection.cursor()
            cursor.execute(
                'INSERT INTO `groups` (name, created_by) VALUES (%s, %s)',
                (group_name, current_user.username)
            )
            group_id = cursor.lastrowid
            
            # Add creator to group
            cursor.execute(
                'INSERT INTO group_members (group_id, username) VALUES (%s, %s)',
                (group_id, current_user.username)
            )
            
            mysql.connection.commit()
            flash('Group created successfully!', 'success')
            return redirect(url_for('list_groups'))
        except Exception as e:
            mysql.connection.rollback()
            app.logger.error(f"Error creating group: {e}")
            flash('An error occurred. Please try again.', 'error')
    
    return render_template('create_group.html', hide_navigation=False)

@app.route('/join-group/<int:group_id>')
@login_required
def join_group(group_id):
    try:
        cursor = mysql.connection.cursor()
        
        # Check if user is already in group
        cursor.execute('''
            SELECT 1 FROM group_members 
            WHERE group_id = %s AND username = %s
        ''', (group_id, current_user.username))
        if cursor.fetchone():
            flash('You are already a member of this group', 'info')
            return redirect(url_for('list_groups'))
        
        # Add user to group
        cursor.execute(
            'INSERT INTO group_members (group_id, username) VALUES (%s, %s)',
            (group_id, current_user.username)
        )
        mysql.connection.commit()
        flash('You have joined the group!', 'success')
    except Exception as e:
        mysql.connection.rollback()
        app.logger.error(f"Error joining group: {e}")
        flash('An error occurred. Please try again.', 'error')
    
    return redirect(url_for('list_groups'))

@app.route('/api/private-messages')
@login_required
def get_private_messages():
    other_user = request.args.get('other_user')
    if not other_user:
        return jsonify({'error': 'Missing other_user parameter'}), 400
    
    try:
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('''
            SELECT * FROM private_messages
            WHERE (sender = %s AND recipient = %s)
            OR (sender = %s AND recipient = %s)
            ORDER BY timestamp ASC
        ''', (current_user.username, other_user, other_user, current_user.username))
        messages = cursor.fetchall()
        return jsonify(messages)
    except Exception as e:
        app.logger.error(f"Error fetching private messages: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/group-messages')
@login_required
def get_group_messages():
    group_id = request.args.get('group_id')
    if not group_id:
        return jsonify({'error': 'Missing group_id parameter'}), 400
    
    try:
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('''
            SELECT id, sender, content, timestamp 
            FROM group_messages 
            WHERE group_id = %s
            ORDER BY timestamp ASC
        ''', (group_id,))
        messages = cursor.fetchall()
        return jsonify(messages)
    except Exception as e:
        app.logger.error(f"Error fetching group messages: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/logout')
@login_required
def logout():
    username = current_user.username
    if username in online_users:
        del online_users[username]
    if username in user_rooms:
        for room in user_rooms[username]:
            leave_room(room)
        del user_rooms[username]
    
    logout_user()
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

@socketio.on('connect')
def handle_connect():
    if current_user.is_authenticated:
        online_users[current_user.username] = request.sid
        user_rooms[current_user.username] = set()
        emit('update_user_list', list(online_users.keys()), broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated:
        username = current_user.username
        if username in online_users:
            del online_users[username]
        if username in user_rooms:
            for room in user_rooms[username]:
                leave_room(room)
            del user_rooms[username]
        emit('update_user_list', list(online_users.keys()), broadcast=True)

@socketio.on('join_room')
def handle_join_room(data):
    if not current_user.is_authenticated:
        return
    
    room_type = data.get('type')
    target = data.get('target')
    username = current_user.username
    
    if room_type == 'private':
        # Private chat room format: sorted usernames
        user1, user2 = sorted([username, target])
        room = f"private_{user1}_{user2}"
    elif room_type == 'group':
        room = f"group_{target}"
    else:
        return
    
    join_room(room)
    if username not in user_rooms:
        user_rooms[username] = set()
    user_rooms[username].add(room)
    
    # Send join notification
    emit('user_joined', {
        'username': username,
        'room': room
    }, room=room)

@socketio.on('leave_room')
def handle_leave_room(data):
    if not current_user.is_authenticated:
        return
    
    room = data.get('room')
    username = current_user.username
    
    if username in user_rooms and room in user_rooms[username]:
        leave_room(room)
        user_rooms[username].remove(room)
        
        # Send leave notification
        emit('user_left', {
            'username': username,
            'room': room
        }, room=room)

@socketio.on('send_message')
def handle_send_message(data):
    if not current_user.is_authenticated:
        return
    
    message = data.get('message', '').strip()
    room_type = data.get('type')
    target = data.get('target')
    username = current_user.username
    
    if not message:
        return
    
    try:
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        
        if room_type == 'private':
            # Create room ID from sorted usernames
            user1, user2 = sorted([username, target])
            room_id = f"private_{user1}_{user2}"
            
            # Store private message
            cursor.execute(
                'INSERT INTO private_messages (sender, recipient, content) VALUES (%s, %s, %s)',
                (username, target, message)
            )
            
            # Get timestamp
            cursor.execute('SELECT NOW() as timestamp')
            timestamp_result = cursor.fetchone()
            timestamp = timestamp_result['timestamp'] if timestamp_result else None
            
            if not timestamp:
                app.logger.error("Failed to get timestamp for private message")
                return
            
            # Prepare message data
            message_data = {
                'sender': username,
                'message': message,
                'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'type': 'private',
                'room': room_id
            }
            
            # Emit to the room
            emit('receive_message', message_data, room=room_id)
                
        elif room_type == 'group':
            room_id = f"group_{target}"
            
            # Store group message
            cursor.execute(
                'INSERT INTO group_messages (sender, group_id, content) VALUES (%s, %s, %s)',
                (username, target, message)
            )
            
            # Get timestamp
            cursor.execute('SELECT NOW() as timestamp')
            timestamp_result = cursor.fetchone()
            timestamp = timestamp_result['timestamp'] if timestamp_result else None
            
            if not timestamp:
                app.logger.error("Failed to get timestamp for group message")
                return
            
            # Get group name
            cursor.execute('SELECT name FROM `groups` WHERE id = %s', (target,))
            group_result = cursor.fetchone()
            
            if not group_result:
                app.logger.error(f"Group not found: {target}")
                return
                
            group_name = group_result['name']
            
            # Prepare message data
            message_data = {
                'sender': username,
                'message': message,
                'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'type': 'group',
                'room': room_id,
                'group_name': group_name,
                'group_id': target
            }
            
            # Emit to all room members
            emit('receive_message', message_data, room=room_id)
        
        mysql.connection.commit()
        
    except Exception as e:
        mysql.connection.rollback()
        app.logger.error(f"Error sending message: {e}")
        app.logger.error(traceback.format_exc())
        emit('error', {'message': 'Failed to send message', 'detail': str(e)})

if __name__ == '__main__':
    socketio.run(app, debug=True)