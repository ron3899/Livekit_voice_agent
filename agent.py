from __future__ import annotations
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    llm
)
from livekit.agents.multimodal import MultimodalAgent
from livekit.plugins import openai, silero
from dotenv import load_dotenv
from api import AssistantFnc
from prompts import WELCOME_MESSAGE, INSTRUCTIONS, LOOKUP_CONTACT_MESSAGE
import os
from pinecone import Pinecone
from langchain_pinecone import PineconeVectorStore
from langchain_openai import OpenAIEmbeddings
import logging


load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("user-data")
logger.setLevel(logging.INFO)

def search_documents(query: str, vector_store: PineconeVectorStore):
    logger.info("התחלת חיפוש במסמכים עם שאילתא: %s", query)
    retriever = vector_store.as_retriever(
        search_type="similarity_score_threshold",
        search_kwargs={"k": 3, "score_threshold": 0.5},
    )
    docs = retriever.invoke(query)
    if docs:
        logger.info("נמצאו %d מסמכים שמתאימים לשאילתא.", len(docs))
    else:
        logger.info("לא נמצאו מסמכים שמתאימים לשאילתא.")
    return docs

async def entrypoint(ctx: JobContext):
    logger.info("התחלת ההתחברות לחדר בשידור חי.")
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)
    await ctx.wait_for_participant()
    logger.info("נמצא משתתף והחיבור הושלם.")

    # Init Pinecone
    logger.info("אתחול Pinecone...")
    pc = Pinecone(api_key=os.environ.get("PINECONE_API_KEY"))
    index_name = os.environ.get("PINECONE_INDEX_NAME")
    index = pc.Index(index_name)

    # Init Vectors
    logger.info("אתחול מודל ההטבעה של OpenAI...")
    embeddings = OpenAIEmbeddings(model="text-embedding-3-large", api_key=os.environ.get("OPENAI_API_KEY"))
    global vector_store
    vector_store = PineconeVectorStore(index=index, embedding=embeddings)
    logger.info("מודל ההטבעה והחנות הוקטורית אותחלו בהצלחה.")

    # Agent init
    model = openai.realtime.RealtimeModel(
        instructions=INSTRUCTIONS,
        voice="shimmer",
        temperature=0.8,
        modalities=["audio", "text"]
    )
    logger.info("מודל Realtime של OpenAI אותחל בהצלחה.")

    assistant_fnc = AssistantFnc()
    assistant = MultimodalAgent(model=model, fnc_ctx=assistant_fnc, vad=silero.VAD.load(min_silence_duration=0.65))
    assistant.start(ctx.room)

    session = model.sessions[0]
    session.conversation.item.create(
        llm.ChatMessage(
            role="assistant",
            content=WELCOME_MESSAGE
        )
    )
    session.response.create()
    logger.info("ההודעה הפותחת נשלחה למשתמש.")

    @session.on("user_speech_committed")
    def on_user_speech_committed(msg: llm.ChatMessage):
        if isinstance(msg.content, list):
            msg.content = "\n".join("[image]" if isinstance(x, llm.ChatImage) else x for x in msg)
        
        logger.info("זוהה דיבור של משתמש: %s", msg.content)

        # Search in documents
        search_results = search_documents(msg.content, vector_store)
        
        if search_results:
            logger.info("תוצאות RAG נמצאו, מסמכים מותאמים הוחזרו.")
            response_content = "\n".join([doc.page_content for doc in search_results])
            session.conversation.item.create(
                llm.ChatMessage(
                    role="assistant",
                    content=response_content
                )
            )
            logger.info("נשלחה תגובת RAG למשתמש.")
        else:
            logger.info("לא נמצאו תוצאות במסמכי ה-RAG.")
            if assistant_fnc.has_contact():
                logger.info("נמצאה איש קשר, מעבד את השאילתה.")
                handle_query(msg)
            else:
                logger.info("אין איש קשר מתאים, מבצע חיפוש בפרופיל.")
                find_profile(msg)

    def find_profile(msg: llm.ChatMessage):
        logger.info("שליחת הודעת חיפוש איש קשר.")
        session.conversation.item.create(
            llm.ChatMessage(
                role="system",
                content=LOOKUP_CONTACT_MESSAGE(msg)
            )
        )
        session.response.create()

    def handle_query(msg: llm.ChatMessage):
        logger.info("מעבד את השאילתה שנמסרה על ידי המשתמש.")
        session.conversation.item.create(
            llm.ChatMessage(
                role="user",
                content=msg.content
            )
        )
        session.response.create()

if __name__ == "__main__":
    logger.info("הפעלת הסוכן...")
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
  
