import os
import io
import logging
import sys
from tempfile import NamedTemporaryFile

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

# Initialize bot
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Conversation states
class TranscriberStates(StatesGroup):
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
    buttons = []
    for name, code in LANGUAGES.items():
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"lang_{code}")])
    buttons.append([InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

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
        "Supported languages: English, Spanish, French, German, Italian, Portuguese, Russian, Japanese, Chinese, Hindi",
        parse_mode="Markdown"
    )

@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "🔊 *How to transcribe audio:*\n\n"
        "1. Send a voice message (microphone icon)\n"
        "2. Or upload an audio file as a document\n"
        "3. Select the language\n"
        "4. I'll transcribe the speech to text\n\n"
        "Send /start to see the welcome message.",
        parse_mode="Markdown"
    )

@dp.message(lambda message: message.voice or message.audio or (message.document and message.document.mime_type and 'audio' in message.document.mime_type))
async def handle_audio(message: types.Message, state: FSMContext):
    try:
        # Get file ID based on message type
        if message.voice:
            file_id = message.voice.file_id
        elif message.audio:
            file_id = message.audio.file_id
        elif message.document:
            file_id = message.document.file_id
        else:
            await message.answer("❌ Please send a voice message or audio file")
            return

        # Show downloading message
        status_msg = await message.answer("📥 Downloading your audio...")

        # Download the audio file
        file = await bot.get_file(file_id)
        file_bytes = await bot.download_file(file.file_path)

        await status_msg.delete()

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
    language_name = [name for name, code in LANGUAGES.items() if code == language_code][0]
    
    user_data = await state.get_data()
    audio_bytes = user_data.get("audio_bytes")

    if not audio_bytes:
        await callback.message.edit_text("❌ Session expired. Please send the audio again.")
        await state.clear()
        await callback.answer()
        return

    await callback.message.edit_text(f"⏳ Transcribing ({language_name})...\nThis may take up to 30 seconds.", parse_mode="Markdown")
    await callback.answer()

    try:
        # Save audio to temporary file
        with NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(audio_bytes)
            tmp.flush()

        # Initialize recognizer
        recognizer = sr.Recognizer()
        
        # Load and transcribe
        with sr.AudioFile(tmp_path) as source:
            # Adjust for ambient noise
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio_data = recognizer.record(source)

        # Recognize speech
        try:
            text = recognizer.recognize_google(audio_data, language=language_code)
            
            if not text or not text.strip():
                text = "⚠️ No speech detected. Please ensure the audio contains clear speech."

        except sr.UnknownValueError:
            text = "❌ Could not understand the audio. Please ensure:\n- The audio contains clear speech\n- The correct language is selected\n- Background noise is minimal"
        except sr.RequestError as e:
            logger.error(f"API request error: {e}")
            text = "⚠️ Transcription service temporarily unavailable. Please try again in a few moments."

        # Clean up
        try:
            os.unlink(tmp_path)
        except:
            pass
            
        await state.clear()
        await callback.message.delete()

        # Send the transcribed text
        response = f"📝 *Transcribed text ({language_name}):*\n\n{text}"
        
        # Split if too long
        if len(response) > 4096:
            for i in range(0, len(response), 4096):
                await callback.message.answer(response[i:i+4096], parse_mode="Markdown")
        else:
            await callback.message.answer(response, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Transcription error: {e}")
        await callback.message.answer("❌ Transcription failed. Please try again with a clearer audio.")
        await state.clear()

@dp.message()
async def unknown_message(message: types.Message):
    """Handle any other messages"""
    await message.answer(
        "🤔 I didn't understand that.\n\n"
        "Send me a voice message or audio file, or type /help for instructions."
    )

async def main():
    """Start the bot"""
    logger.info("🚀 Starting Audio to Text Bot...")
    logger.info("Bot is polling for messages...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
