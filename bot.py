
import discord
from discord.ui import Button, View, Modal, TextInput
from discord.ext import commands, tasks
import sqlite3
from datetime import datetime, timedelta
import random
import string
from flask import Flask, jsonify, request
from flask_cors import CORS
import threading
from dotenv import load_dotenv
import os

# Charger les variables d'environnement
load_dotenv()

# Initialize intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Required for role management
bot = commands.Bot(command_prefix="!", intents=intents)

# SQLite database
db = sqlite3.connect("keys.db", check_same_thread=False)
cursor = db.cursor()

# Create table for keys (with android_uid)
cursor.execute('''CREATE TABLE IF NOT EXISTS keys (
    key TEXT PRIMARY KEY,
    user_id TEXT,
    expiration TEXT,
    status TEXT,
    registration_date TEXT,
    android_uid TEXT
)''')

# Create table for banned users
cursor.execute('''CREATE TABLE IF NOT EXISTS banned_users (
    user_id TEXT PRIMARY KEY
)''')

# Create table for maintenance mode
cursor.execute('''CREATE TABLE IF NOT EXISTS maintenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    active BOOLEAN NOT NULL,
    end_time TEXT,
    last_updated TEXT
)''')
db.commit()

# Initialize maintenance state if not exists
cursor.execute("SELECT * FROM maintenance WHERE id = 1")
if not cursor.fetchone():
    cursor.execute("INSERT INTO maintenance (id, active, end_time, last_updated) VALUES (?, ?, ?, ?)",
                   (1, False, None, datetime.now().isoformat()))
    db.commit()

# Admin role, Guild, and VIP role IDs
ADMIN_ROLE_ID = "1305384766459215893"
GUILD_ID = "1305375757681561640"
VIP_ROLE_ID = "1352820677224431737"

# Flask application for API
app = Flask(__name__)
CORS(app)

# Function to check maintenance status
def is_maintenance_active():
    cursor.execute("SELECT active, end_time FROM maintenance WHERE id = 1")
    row = cursor.fetchone()
    if not row:
        return False
    active, end_time = row
    if not active or not end_time:
        return False
    end_time = datetime.fromisoformat(end_time)
    return active and datetime.now() < end_time

# Route to check maintenance status
@app.route('/check_maintenance', methods=['GET'])
def check_maintenance():
    cursor.execute("SELECT active, end_time FROM maintenance WHERE id = 1")
    row = cursor.fetchone()
    if not row:
        return jsonify({"active": False, "end_time": None}), 200
    active, end_time = row
    if not active or not end_time:
        return jsonify({"active": False, "end_time": None}), 200
    end_time_dt = datetime.fromisoformat(end_time)
    if datetime.now() > end_time_dt:
        cursor.execute("UPDATE maintenance SET active = ?, end_time = ? WHERE id = ?", (False, None, 1))
        db.commit()
        return jsonify({"active": False, "end_time": None}), 200
    return jsonify({"active": True, "end_time": end_time}), 200

@app.route('/check_key', methods=['GET'])
def check_key():
    if is_maintenance_active():
        return jsonify({"error": "Server under maintenance"}), 503
    key = request.args.get('key')
    cursor.execute("SELECT * FROM keys WHERE key = ?", (key,))
    row = cursor.fetchone()
    if row:
        return jsonify({
            "key": row[0],
            "user_id": row[1],
            "expiration": row[2],
            "status": row[3],
            "registration_date": row[4]
        })
    return jsonify({"error": "Invalid key"}), 404

@app.route('/check_uid', methods=['GET'])
def check_uid():
    if is_maintenance_active():
        return jsonify({"error": "Server under maintenance"}), 503
    key = request.args.get('key')
    android_uid = request.args.get('android_uid')

    if not key or not android_uid:
        return jsonify({"error": "Invalid request"}), 400

    cursor.execute("SELECT android_uid FROM keys WHERE key = ?", (key,))
    row = cursor.fetchone()
    if not row:
        return jsonify({"error": "Invalid key"}), 404
    
    if row[0]:
        registered_uid = row[0]
        if registered_uid != android_uid:
            return jsonify({"error": "Key already in use"}), 403
        return jsonify({"exists": True}), 200
    return jsonify({"exists": False}), 200

@app.route('/register_uid', methods=['GET', 'POST'])
def register_uid():
    if is_maintenance_active():
        return jsonify({"error": "Server under maintenance"}), 503
    try:
        if request.method == 'POST':
            data = request.get_json()
            if not data:
                return jsonify({"error": "Invalid request"}), 400
            key = data.get('key')
            discord_id = data.get('discord_id')
            android_uid = data.get('android_uid')
        else:  # GET
            key = request.args.get('key')
            discord_id = request.args.get('discord_id')
            android_uid = request.args.get('android_uid')
        
        if not key or not discord_id or not android_uid:
            return jsonify({"error": "Invalid request"}), 400
        
        # Check if user is banned
        cursor.execute("SELECT * FROM banned_users WHERE user_id = ?", (discord_id,))
        if cursor.fetchone():
            return jsonify({"error": "Access denied"}), 403

        cursor.execute("SELECT user_id FROM keys WHERE key = ?", (key,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Invalid key"}), 404
        
        cursor.execute("UPDATE keys SET android_uid = ?, user_id = ? WHERE key = ?",
                       (android_uid, discord_id, key))
        db.commit()
        
        log_channel = discord.utils.get(bot.get_guild(int(GUILD_ID)).channels, name="logs")
        if log_channel:
            ip_address = request.remote_addr
            bot.loop.create_task(log_channel.send(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] User {discord_id} registered UID with key {key} | IP: {ip_address}"))
        
        # Add VIP role to user
        guild = bot.get_guild(int(GUILD_ID))
        member = guild.get_member(int(discord_id))
        if member:
            vip_role = guild.get_role(int(VIP_ROLE_ID))
            if vip_role and vip_role not in member.roles:
                bot.loop.create_task(member.add_roles(vip_role))
                bot.loop.create_task(log_channel.send(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] VIP role added to user {discord_id} | IP: {ip_address}"))

        return jsonify({"success": "UID registered"}), 200
    except Exception as e:
        return jsonify({"error": "Server error"}), 500

@app.route('/log_usage', methods=['GET', 'POST'])
def log_usage():
    if is_maintenance_active():
        return jsonify({"error": "Server under maintenance"}), 503
    try:
        if request.method == 'POST':
            data = request.get_json()
            if not data:
                return jsonify({"error": "Invalid request"}), 400
            key = data.get('key')
            action = data.get('action')
        else:  # GET
            key = request.args.get('key')
            action = request.args.get('action')
        
        if not key or not action:
            return jsonify({"error": "Invalid request"}), 400
        
        cursor.execute("SELECT user_id FROM keys WHERE key = ?", (key,))
        row = cursor.fetchone()
        discord_id = row[0] if row else "Unknown"

        log_channel = discord.utils.get(bot.get_guild(int(GUILD_ID)).channels, name="logs")
        if log_channel:
            ip_address = request.remote_addr
            bot.loop.create_task(log_channel.send(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Key `{key}` used action: {action} | Discord ID: {discord_id} | IP: {ip_address}"))
        
        return jsonify({"success": "Logged"}), 200
    except Exception as e:
        return jsonify({"error": "Server error"}), 500

@app.route('/script_execution', methods=['GET', 'POST'])
def script_execution():
    if is_maintenance_active():
        return jsonify({"error": "Server under maintenance"}), 503
    try:
        if request.method == 'POST':
            data = request.get_json()
            if not data:
                return jsonify({"error": "Invalid request"}), 400
            key = data.get('key')
        else:  # GET
            key = request.args.get('key')
        
        if not key:
            return jsonify({"error": "Invalid request"}), 400
        
        cursor.execute("SELECT user_id FROM keys WHERE key = ?", (key,))
        row = cursor.fetchone()
        discord_id = row[0] if row else "Unknown"

        log_channel = discord.utils.get(bot.get_guild(int(GUILD_ID)).channels, name="logs")
        if log_channel:
            ip_address = request.remote_addr
            bot.loop.create_task(log_channel.send(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Script executed with key `{key}` | Discord ID: {discord_id} | IP: {ip_address}"))
        
        return jsonify({"success": "Execution logged"}), 200
    except Exception as e:
        return jsonify({"error": "Server error"}), 500

# Generate a unique key
def generate_unique_key():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

# Check if user is admin
def is_admin(user):
    return any(role.id == int(ADMIN_ROLE_ID) for role in user.roles)

# View for ticket actions (including Close button for admins)
class TicketActionsView(View):
    def __init__(self):
        super().__init__(timeout=None)  # Persistent view

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Only admins can close tickets!", ephemeral=True)
            return
        
        channel = interaction.channel
        await channel.send(f"Ticket closed by {interaction.user.mention}.")
        log_channel = discord.utils.get(interaction.guild.channels, name="logs")
        if log_channel:
            await log_channel.send(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Ticket {channel.name} closed by {interaction.user.mention}")
        await channel.delete()

# Buttons for tickets
class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)  # Persistent view

    @discord.ui.button(label="Report a Bug", style=discord.ButtonStyle.red, custom_id="report_bug")
    async def report_bug(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        user = interaction.user
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.get_role(int(ADMIN_ROLE_ID)): discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        ticket_channel = await guild.create_text_channel(
            f"bug-{user.name}",
            overwrites=overwrites,
            topic=f"Bug report by {user.name}",
            category=discord.utils.get(guild.categories, name="Tickets")
        )
        await ticket_channel.send(f"Bug report ticket created by {user.mention}. Please describe the issue with the script in detail.", view=TicketActionsView())
        await interaction.response.send_message(f"Your bug report ticket has been created: {ticket_channel.mention}", ephemeral=True)

    @discord.ui.button(label="Request Payment Info", style=discord.ButtonStyle.green, custom_id="request_payment")
    async def request_payment(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        user = interaction.user
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.get_role(int(ADMIN_ROLE_ID)): discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        ticket_channel = await guild.create_text_channel(
            f"payment-{user.name}",
            overwrites=overwrites,
            topic=f"Payment request by {user.name}",
            category=discord.utils.get(guild.categories, name="Tickets")
        )
        await ticket_channel.send(f"Payment request ticket created by {user.mention}. Please specify your payment method and the plan you are interested in (e.g., 7-day VIP key).", view=TicketActionsView())
        await interaction.response.send_message(f"Your payment request ticket has been created: {ticket_channel.mention}", ephemeral=True)

# Buttons for admin interface
class AdminView(View):
    def __init__(self):
        super().__init__(timeout=None)  # Persistent view

    @discord.ui.button(label="Add Key", style=discord.ButtonStyle.green, custom_id="add_key")
    async def add_key(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Only admins can use this!", ephemeral=True)
            return
        await interaction.response.send_modal(AddKeyModal())

    @discord.ui.button(label="Check Key", style=discord.ButtonStyle.blurple, custom_id="check_key")
    async def check_key(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Only admins can use this!", ephemeral=True)
            return
        await interaction.response.send_modal(CheckKeyModal())

    @discord.ui.button(label="Extend Key", style=discord.ButtonStyle.blurple, custom_id="extend_key")
    async def extend_key(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Only admins can use this!", ephemeral=True)
            return
        await interaction.response.send_modal(ExtendKeyModal())

    @discord.ui.button(label="Delete Key", style=discord.ButtonStyle.red, custom_id="delete_key")
    async def delete_key(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Only admins can use this!", ephemeral=True)
            return
        await interaction.response.send_modal(DeleteKeyModal())

    @discord.ui.button(label="List Keys", style=discord.ButtonStyle.blurple, custom_id="list_keys")
    async def list_keys(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Only admins can use this!", ephemeral=True)
            return
        cursor.execute("SELECT * FROM keys WHERE status = 'active'")
        keys = cursor.fetchall()
        if keys:
            keys_list = "\n".join([f"Key: `{k[0]}` | User: <@{k[1]}> | Registered: {k[4].split('T')[0]} | Expires: {k[2].split('T')[0]}" for k in keys])
            await interaction.response.send_message(f"**Active Keys:**\n{keys_list}", ephemeral=True)
        else:
            await interaction.response.send_message("No active keys found.", ephemeral=True)

    @discord.ui.button(label="Revoke Key", style=discord.ButtonStyle.red, custom_id="revoke_key")
    async def revoke_key(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Only admins can use this!", ephemeral=True)
            return
        await interaction.response.send_modal(RevokeKeyModal())

    @discord.ui.button(label="Ban User", style=discord.ButtonStyle.red, custom_id="ban_user")
    async def ban_user(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Only admins can use this!", ephemeral=True)
            return
        await interaction.response.send_modal(BanUserModal())

    @discord.ui.button(label="Maintenance", style=discord.ButtonStyle.grey, custom_id="maintenance")
    async def maintenance(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("Only admins can use this!", ephemeral=True)
            return
        await interaction.response.send_modal(MaintenanceModal())

# Modals for input
class AddKeyModal(Modal, title="Add a VIP Key"):
    duration = TextInput(label="Duration (days)", placeholder="e.g., 7")
    user_id = TextInput(label="User ID", placeholder="e.g., 123456789")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            duration_days = int(self.duration.value)
            user_id = self.user_id.value

            # Check if user is banned
            cursor.execute("SELECT * FROM banned_users WHERE user_id = ?", (user_id,))
            if cursor.fetchone():
                await interaction.response.send_message("This user is banned and cannot receive a key!", ephemeral=True)
                return

            key = generate_unique_key()
            expiration = datetime.now() + timedelta(days=duration_days)
            registration_date = datetime.now().isoformat()
            cursor.execute("INSERT INTO keys (key, user_id, expiration, status, registration_date, android_uid) VALUES (?, ?, ?, ?, ?, ?)",
                           (key, user_id, expiration.isoformat(), "active", registration_date, None))
            db.commit()
            user = await bot.fetch_user(int(user_id))
            await user.send(f"Your VIP Key: `{key}`\nExpires on: {expiration.strftime('%Y-%m-%d')}")
            await interaction.response.send_message(f"Key sent to <@{user_id}>!", ephemeral=True)
            
            keys_channel = discord.utils.get(interaction.guild.channels, name="keys")
            if keys_channel:
                await keys_channel.send(f"Key: `{key}`\nUser: {user.name} (<@{user_id}>)\nRegistered: {registration_date.split('T')[0]}\nExpires: {expiration.strftime('%Y-%m-%d')}")
        except ValueError:
            await interaction.response.send_message("Duration must be an integer!", ephemeral=True)

class CheckKeyModal(Modal, title="Check a VIP Key"):
    key = TextInput(label="Key", placeholder="e.g., ABC123")

    async def on_submit(self, interaction: discord.Interaction):
        cursor.execute("SELECT * FROM keys WHERE key = ?", (self.key.value,))
        row = cursor.fetchone()
        if row:
            user_id, expiration, status, registration_date = row[1], row[2], row[3], row[4]
            await interaction.response.send_message(
                f"Key: `{self.key.value}`\nUser: <@{user_id}>\nRegistered: {registration_date.split('T')[0]}\nExpiration: {expiration.split('T')[0]}\nStatus: {status}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("Key not found.", ephemeral=True)

class ExtendKeyModal(Modal, title="Extend a VIP Key"):
    key = TextInput(label="Key", placeholder="e.g., ABC123")
    duration = TextInput(label="Additional Days", placeholder="e.g., 7")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            extra_days = int(self.duration.value)
            cursor.execute("SELECT * FROM keys WHERE key = ?", (self.key.value,))
            row = cursor.fetchone()
            if row:
                current_expiration = datetime.fromisoformat(row[2])
                new_expiration = current_expiration + timedelta(days=extra_days)
                cursor.execute("UPDATE keys SET expiration = ? WHERE key = ?", (new_expiration.isoformat(), self.key.value))
                db.commit()
                await interaction.response.send_message(f"Key `{self.key.value}` extended until {new_expiration.strftime('%Y-%m-%d')}", ephemeral=True)
                
                keys_channel = discord.utils.get(interaction.guild.channels, name="keys")
                if keys_channel:
                    await keys_channel.send(f"Key `{self.key.value}` extended until {new_expiration.strftime('%Y-%m-%d')}")
            else:
                await interaction.response.send_message("Key not found.", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("Duration must be an integer!", ephemeral=True)

class DeleteKeyModal(Modal, title="Delete a VIP Key"):
    key = TextInput(label="Key", placeholder="e.g., ABC123")

    async def on_submit(self, interaction: discord.Interaction):
        cursor.execute("SELECT user_id FROM keys WHERE key = ?", (self.key.value,))
        row = cursor.fetchone()
        if row:
            user_id = row[0]
            cursor.execute("DELETE FROM keys WHERE key = ?", (self.key.value,))
            db.commit()
            await interaction.response.send_message(f"Key `{self.key.value}` deleted.", ephemeral=True)
            
            keys_channel = discord.utils.get(interaction.guild.channels, name="keys")
            if keys_channel:
                await keys_channel.send(f"Key `{self.key.value}` deleted.")

            # Remove VIP role from user
            guild = interaction.guild
            member = guild.get_member(int(user_id))
            if member:
                vip_role = guild.get_role(int(VIP_ROLE_ID))
                if vip_role and vip_role in member.roles:
                    await member.remove_roles(vip_role)
                    log_channel = discord.utils.get(guild.channels, name="logs")
                    if log_channel:
                        await log_channel.send(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] VIP role removed from user {user_id} due to key deletion")
        else:
            await interaction.response.send_message("Key not found.", ephemeral=True)

class RevokeKeyModal(Modal, title="Revoke a VIP Key"):
    key = TextInput(label="Key", placeholder="e.g., ABC123")

    async def on_submit(self, interaction: discord.Interaction):
        cursor.execute("SELECT user_id FROM keys WHERE key = ?", (self.key.value,))
        row = cursor.fetchone()
        if row:
            user_id = row[0]
            cursor.execute("UPDATE keys SET status = 'inactive' WHERE key = ?", (self.key.value,))
            db.commit()
            await interaction.response.send_message(f"Key `{self.key.value}` has been revoked.", ephemeral=True)

            # Remove VIP role from user
            guild = interaction.guild
            member = guild.get_member(int(user_id))
            if member:
                vip_role = guild.get_role(int(VIP_ROLE_ID))
                if vip_role and vip_role in member.roles:
                    await member.remove_roles(vip_role)
                    log_channel = discord.utils.get(guild.channels, name="logs")
                    if log_channel:
                        await log_channel.send(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] VIP role removed from user {user_id} due to key revocation")
        else:
            await interaction.response.send_message("Key not found.", ephemeral=True)

class BanUserModal(Modal, title="Ban a User"):
    user_id = TextInput(label="User ID", placeholder="e.g., 123456789")

    async def on_submit(self, interaction: discord.Interaction):
        user_id = self.user_id.value
        cursor.execute("INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)", (user_id,))
        cursor.execute("DELETE FROM keys WHERE user_id = ?", (user_id,))
        db.commit()
        await interaction.response.send_message(f"User <@{user_id}> has been banned and all their keys have been deleted.", ephemeral=True)

        # Remove VIP role from user
        guild = interaction.guild
        member = guild.get_member(int(user_id))
        if member:
            vip_role = guild.get_role(int(VIP_ROLE_ID))
            if vip_role and vip_role in member.roles:
                await member.remove_roles(vip_role)
                log_channel = discord.utils.get(guild.channels, name="logs")
                if log_channel:
                    await log_channel.send(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] VIP role removed from user {user_id} due to ban")

class MaintenanceModal(Modal, title="Manage Maintenance Mode"):
    action = TextInput(label="Action (enable/disable/add_time)", placeholder="e.g., enable")
    duration = TextInput(label="Duration (hours, if enabling/adding)", placeholder="e.g., 24", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        action = self.action.value.lower()
        log_channel = discord.utils.get(interaction.guild.channels, name="logs")

        if action not in ["enable", "disable", "add_time"]:
            await interaction.response.send_message("Invalid action! Use 'enable', 'disable', or 'add_time'.", ephemeral=True)
            return

        if action == "disable":
            cursor.execute("UPDATE maintenance SET active = ?, end_time = ?, last_updated = ? WHERE id = ?",
                           (False, None, datetime.now().isoformat(), 1))
            db.commit()
            await interaction.response.send_message("Maintenance mode disabled.", ephemeral=True)
            if log_channel:
                await log_channel.send(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Maintenance mode disabled by {interaction.user.mention}")
            return

        if not self.duration.value:
            await interaction.response.send_message("Duration is required for enabling or adding time!", ephemeral=True)
            return

        try:
            duration_hours = int(self.duration.value)
            if duration_hours <= 0:
                raise ValueError("Duration must be positive!")
        except ValueError:
            await interaction.response.send_message("Duration must be a positive integer (in hours)!", ephemeral=True)
            return

        if action == "enable":
            end_time = datetime.now() + timedelta(hours=duration_hours)
            cursor.execute("UPDATE maintenance SET active = ?, end_time = ?, last_updated = ? WHERE id = ?",
                           (True, end_time.isoformat(), datetime.now().isoformat(), 1))
            db.commit()
            await interaction.response.send_message(f"Maintenance mode enabled until {end_time.strftime('%Y-%m-%d %H:%M:%S')}.", ephemeral=True)
            if log_channel:
                await log_channel.send(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Maintenance mode enabled by {interaction.user.mention} until {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        elif action == "add_time":
            cursor.execute("SELECT active, end_time FROM maintenance WHERE id = 1")
            row = cursor.fetchone()
            if not row or not row[0]:
                await interaction.response.send_message("Maintenance mode is not active! Enable it first.", ephemeral=True)
                return
            current_end_time = datetime.fromisoformat(row[1])
            if datetime.now() > current_end_time:
                await interaction.response.send_message("Maintenance mode has already ended! Enable it again.", ephemeral=True)
                return
            new_end_time = current_end_time + timedelta(hours=duration_hours)
            cursor.execute("UPDATE maintenance SET end_time = ?, last_updated = ? WHERE id = ?",
                           (new_end_time.isoformat(), datetime.now().isoformat(), 1))
            db.commit()
            await interaction.response.send_message(f"Maintenance time extended until {new_end_time.strftime('%Y-%m-%d %H:%M:%S')}.", ephemeral=True)
            if log_channel:
                await log_channel.send(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Maintenance time extended by {interaction.user.mention} until {new_end_time.strftime('%Y-%m-%d %H:%M:%S')}")

# Task to check expired keys
@tasks.loop(minutes=60)  # Check every hour
async def check_expired_keys():
    cursor.execute("SELECT * FROM keys WHERE status = 'active'")
    keys = cursor.fetchall()
    guild = bot.get_guild(int(GUILD_ID))
    log_channel = discord.utils.get(guild.channels, name="logs")

    for key in keys:
        key_value, user_id, expiration, status, registration_date, android_uid = key
        expiration_date = datetime.fromisoformat(expiration)
        if datetime.now() > expiration_date:
            cursor.execute("UPDATE keys SET status = 'inactive' WHERE key = ?", (key_value,))
            db.commit()
            if log_channel:
                await log_channel.send(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Key `{key_value}` has expired for user {user_id}")

            # Remove VIP role from user
            member = guild.get_member(int(user_id))
            if member:
                vip_role = guild.get_role(int(VIP_ROLE_ID))
                if vip_role and vip_role in member.roles:
                    await member.remove_roles(vip_role)
                    if log_channel:
                        await log_channel.send(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] VIP role removed from user {user_id} due to key expiration")

# Task to refresh messages periodically to prevent interaction expiration
@tasks.loop(minutes=10)
async def refresh_messages():
    guild = bot.get_guild(int(GUILD_ID))
    if not guild:
        return

    admin_channel = discord.utils.get(guild.channels, name="admin")
    tickets_channel = discord.utils.get(guild.channels, name="tickets")

    if admin_channel:
        async for message in admin_channel.history(limit=10):
            if message.author == bot.user and message.embeds and "Admin Controls" in message.embeds[0].title:
                admin_embed = discord.Embed(
                    title="Admin Controls",
                    description="Manage VIP keys, users, and maintenance with the buttons below.",
                    color=discord.Color.red()
                )
                admin_embed.set_footer(text="Powered by ZLI Hacks")
                await message.edit(embed=admin_embed, view=AdminView())
                break

    if tickets_channel:
        async for message in tickets_channel.history(limit=10):
            if message.author == bot.user and message.embeds and "Support Tickets" in message.embeds[0].title:
                ticket_embed = discord.Embed(
                    title="Support Tickets",
                    description="Open a ticket to report a bug or request payment information.",
                    color=discord.Color.red()
                )
                ticket_embed.set_footer(text="Powered by ZLI Hacks")
                await message.edit(embed=ticket_embed, view=TicketView())
                break

# Register persistent views
def setup_persistent_views():
    bot.add_view(AdminView())
    bot.add_view(TicketView())
    bot.add_view(TicketActionsView())

# Startup event with channel setup
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    guild = bot.get_guild(int(GUILD_ID))
    if not guild:
        print("Guild not found! Check GUILD_ID.")
        return

    # Register persistent views
    setup_persistent_views()

    # Start the refresh task
    if not refresh_messages.is_running():
        refresh_messages.start()

    # Define permissions for private channels (logs and keys)
    private_overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.get_role(int(ADMIN_ROLE_ID)): discord.PermissionOverwrite(view_channel=True, send_messages=True)
    }

    management_category = discord.utils.get(guild.categories, name="ZLI Management")
    if not management_category:
        management_category = await guild.create_category("ZLI Management")
        print("Category 'ZLI Management' created.")

    tickets_category = discord.utils.get(guild.categories, name="Tickets")
    if not tickets_category:
        tickets_category = await guild.create_category("Tickets")
        print("Category 'Tickets' created.")

    admin_channel = discord.utils.get(guild.channels, name="admin")
    if not admin_channel:
        admin_channel = await guild.create_text_channel(
            "admin",
            category=management_category,
            overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.get_role(int(ADMIN_ROLE_ID)): discord.PermissionOverwrite(view_channel=True, send_messages=True)
            }
        )
        print("Channel 'admin' created.")

    # Check if an admin message already exists
    admin_message = None
    async for message in admin_channel.history(limit=10):
        if message.author == bot.user and message.embeds and "Admin Controls" in message.embeds[0].title:
            admin_message = message
            break

    admin_embed = discord.Embed(
        title="Admin Controls",
        description="Manage VIP keys, users, and maintenance with the buttons below.",
        color=discord.Color.red()
    )
    admin_embed.set_footer(text="Powered by ZLI Hacks")
    
    if admin_message:
        # Update the existing message
        await admin_message.edit(embed=admin_embed, view=AdminView())
        print("Updated existing admin message.")
    else:
        # Send a new message if none exists
        await admin_channel.send(embed=admin_embed, view=AdminView())
        print("Sent new admin message.")

    tickets_channel = discord.utils.get(guild.channels, name="buy-hack-ticket")
    if not tickets_channel:
        tickets_channel = await guild.create_text_channel("buy-hack-ticket", category=tickets_category)
        print("Channel 'buy-hack-ticket' created.")

    # Check if a tickets message already exists
    tickets_message = None
    async for message in tickets_channel.history(limit=10):
        if message.author == bot.user and message.embeds and "Support Tickets" in message.embeds[0].title:
            tickets_message = message
            break

    ticket_embed = discord.Embed(
        title="Support Tickets",
        description="Open a ticket to report a bug or request payment information.",
        color=discord.Color.red()
    )
    ticket_embed.set_footer(text="Powered by ZLI Hacks")
    
    if tickets_message:
        # Update the existing message
        await tickets_message.edit(embed=ticket_embed, view=TicketView())
        print("Updated existing tickets message.")
    else:
        # Send a new message if none exists
        await tickets_channel.send(embed=ticket_embed, view=TicketView())
        print("Sent new tickets message.")

    logs_channel = discord.utils.get(guild.channels, name="logs")
    if not logs_channel:
        logs_channel = await guild.create_text_channel(
            "logs",
            category=management_category,
            overwrites=private_overwrites
        )
        print("Channel 'logs' created.")
    # Only send the initial log message if the channel is empty
    async for message in logs_channel.history(limit=1):
        if message.author == bot.user:
            break
    else:
        await logs_channel.send("Logs will appear here when the script is executed or actions are performed.")

    keys_channel = discord.utils.get(guild.channels, name="keys")
    if not keys_channel:
        keys_channel = await guild.create_text_channel(
            "keys",
            category=management_category,
            overwrites=private_overwrites
        )
        print("Channel 'keys' created.")
    
    # Only send the keys message if the channel is empty
    async for message in keys_channel.history(limit=1):
        if message.author == bot.user:
            break
    else:
        cursor.execute("SELECT * FROM keys")
        keys = cursor.fetchall()
        if keys:
            keys_list = "\n".join([f"Key: `{k[0]}` | User: <@{k[1]}> | Registered: {k[4].split('T')[0]} | Expires: {k[2].split('T')[0]} | Status: {k[3]}" for k in keys])
            await keys_channel.send(f"**Existing Keys:**\n{keys_list}")
        else:
            await keys_channel.send("No keys registered yet.")

    # Start the task to check expired keys
    if not check_expired_keys.is_running():
        check_expired_keys.start()

# Error handler for interactions
@bot.event
async def on_interaction_error(interaction: discord.Interaction, error: Exception):
    await interaction.response.send_message("An error occurred while processing your request. Please try again later.", ephemeral=True)
    print(f"Interaction error: {error}")

# Connection monitoring
@bot.event
async def on_disconnect():
    print("Bot disconnected from Discord.")

@bot.event
async def on_connect():
    print("Bot connected to Discord.")

@bot.event
async def on_resumed():
    print("Bot session resumed.")

# Start Flask and the bot
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5031))
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port)).start()
    bot.run(os.getenv("DISCORD_TOKEN"))
