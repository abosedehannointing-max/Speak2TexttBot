import os
import logging
import sys
import io
from tempfile import NamedTemporaryFile

from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv
import speech_recognition as sr

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Check for BOT_TOKEN
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN environment variable is not set!")
    sys.exit(1)
else:
    logger.info(f"✅ BOT_TOKEN found (first 10 chars: {BOT_TOKEN[:10]})")

# Initialize Flask app (for Render health checks)
app = Flask(__name__)

# Initialize bot
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Conversation states
class TranscriberStates(StatesGroup):
    waiting_for_audio = State()
    waiting_for_language = State()

# Supported languages
LANGUAGES = {
    "🇺🇸 English": "en-US",
    "🇪🇸 Spanish": "es-ES",
    "🇫🇷 French": "fr-FR",
    "🇩🇪 German": "de-DE",
    "🇮🇹 Italian": "it-IT",
    "🇵🇹 Portuguese": "pt-PT",
    "🇷🇺 Russian": "ru-RU",
    "🇯🇵 Japanese": "ja-JP",
    "🇨🇳 Chinese": "zh-CN",
    "🇮🇳 Hindi": "hi-IN"
}

def get_language_keyboard():
    """Create inline keyboard with language options"""
    buttons = []
    for name, code in LANGUAGES.items():
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"lang_{code}")])
    buttons.append([InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@app.route('/')
@app.route('/health')
def health_check():
    return "Audio to Text Bot is running", 200

@dp.message(Command("start"))
async def start_command(message: types.Message):
    logger.info(f"Received /start from user {message.from_user.id}")
    await message.answer(
        "🎤 *Audio to Text Transcriber Bot*\n\n"
        "Send me a voice message or audio file, and I'll transcribe it to text.\n\n"
        "📌 *How to use:*\n"
        "1. Send a voice message or upload an audio file (MP3, WAV, OGG, M4A)\n"
        "2. Choose the language of the audio\n"
        "3. I'll send back the transcribed text\n\n"
        "⚙️ *Supported formats:* MP3, WAV, OGG, M4A, FLAC\n"
        "📁 Max file size: 20 MB\n\n"
        "Supported languages: English, Spanish, French, German, Italian, Portuguese, Russian, Japanese, Chinese, Hindi",
        parse_mode="Markdown"
    )

@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "🔊 *How to transcribe audio:*\n\n"
        "1. Send a voice message (press the microphone icon in Telegram)\n"
        "2. Or upload an audio file as a document\n"
        "3. Select the language of the audio\n"
        "4. Wait a moment – I'll transcribe the speech to text\n\n"
        "📝 *Tips:*\n"
        "- Speak clearly and minimize background noise\n"
        "- Keep audio under 2 minutes for best results\n"
        "- Use high-quality recordings for better accuracy\n\n"
        "Send /start to see the welcome message again.",
        parse_mode="Markdown"
    )

@dp.message(lambda message: message.voice or (message.audio or message.document))
async def handle_audio(message: types.Message, state: FSMContext):
    try:
        # Handle different audio types
        if message.voice:
            file_id = message.voice.file_id
            file_type = "voice"
        elif message.audio:
            file_id = message.audio.file_id
            file_type = "audio"
        elif message.document:
            # Check if document is an audio file
            allowed_mimes = ['audio/mpeg', 'audio/wav', 'audio/ogg', 'audio/x-wav', 'audio/mp4', 'audio/flac']
            if message.document.mime_type not in allowed_mimes:
                await message.answer("❌ Please send an audio file (MP3, WAV, OGG, M4A, FLAC)")
                return
            file_id = message.document.file_id
            file_type = "document"
        else:
            await message.answer("❌ Please send a voice message or audio file")
            return

        # Show "processing" message
        processing_msg = await message.answer("📥 Downloading your audio...")

        # Download the audio file
        file = await bot.get_file(file_id)
        file_bytes = await bot.download_file(file.file_path)

        await processing_msg.delete()

        # Store in state
        await state.update_data(audio_bytes=file_bytes.getvalue())
        await state.set_state(TranscriberStates.waiting_for_language)

        # Ask for language
        await message.answer(
            "🌍 Select the language of the audio:",
            reply_markup=get_language_keyboard()
        )

    except Exception as e:
        logger.error(f"Error handling audio: {e}")
        await message.answer("❌ Failed to process your audio. Please try again.")

@dp.callback_query(TranscriberStates.waiting_for_language)
async def process_language_selection(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "cancel":
        await state.clear()
        await callback.message.edit_text("❌ Transcription cancelled.")
        await callback.answer()
        return

    language_code = callback.data.replace("lang_", "")
    user_data = await state.get_data()
    audio_bytes = user_data.get("audio_bytes")

    if not audio_bytes:
        await callback.message.edit_text("❌ Session expired. Please send the audio again.")
        await state.clear()
        await callback.answer()
        return

    await callback.message.edit_text(f"⏳ Transcribing with *{language_code}*...", parse_mode="Markdown")
    await callback.answer()

    try:
        # Save audio to temporary file
        with NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(audio_bytes)
            tmp.flush()

        # Initialize recognizer
        recognizer = sr.Recognizer()
        
        # Load audio file
        with sr.AudioFile(tmp_path) as source:
            # Adjust for ambient noise
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio_data = recognizer.record(source)

        # Recognize speech
        try:
            # Use Google Web Speech API (free, no API key required)
            text = recognizer.recognize_google(audio_data, language=language_code)
            
            if not text.strip():
                text = "⚠️ No speech detected. Please ensure the audio contains clear speech and try again."

        except sr.UnknownValueError:
            text = "❌ Could not understand the audio. Please ensure:\n- The audio contains clear speech\n- The correct language is selected\n- Background noise is minimal"
        except sr.RequestError as e:
            logger.error(f"API request error: {e}")
            text = "⚠️ Transcription service temporarily unavailable. Please try again in a few moments."

        # Clean up
        os.unlink(tmp_path)
        await state.clear()
        await callback.message.delete()

        # Send the transcribed text
        response_text = f"📝 *Transcribed text ({language_code}):*\n\n{text}"
        
        # If text is too long, split into multiple messages
        if len(response_text) > 4000:
            for i in range(0, len(response_text), 4000):
                await callback.message.answer(response_text[i:i+4000], parse_mode="Markdown")
        else:
            await callback.message.answer(response_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Transcription error: {e}")
        await callback.message.answer("❌ Transcription failed. Please try again with a clearer audio.")
        await state.clear()

@dp.callback_query()
async def invalid_callback(callback: types.CallbackQuery):
    """Handle invalid callbacks"""
    await callback.answer("Please send an audio file first using /start", show_alert=False)

async def run_bot():
    """Run the bot with proper error handling"""
    try:
        logger.info("🚀 Starting Audio to Text Bot polling...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"❌ Bot polling failed: {e}")
        raise

if __name__ == "__main__":
    import asyncio
    import threading
    
    # Run Flask in a separate thread (for health checks)
    def run_flask():
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port, use_reloader=False)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run the bot
    asyncio.run(run_bot())
