Insurance Sales Portfolio Expert: Architecture & Design

This document details the technical architecture, operational workflows, and future roadmap for the Insurance Sales AI Agent.

---

## System Architecture

The application is built using a modular approach to ensure simplicity, explainability and high performance. 

### 1. Frontend & Orchestration
- **Technology**: 
- **Role**: Serves as the user interface and central controller. It manages:
  - **Session State**: Tracks chat history, processed audio IDs, and indexed document status.
  - **Resource Caching**: Uses `@st.cache_resource` to ensure heavy models (Whisper, RAG, LLM) are loaded only once.
  - **Component Coordination**: Pipes data from the microphone to Whisper, then to the RAG engine, then to the LLM, and finally to the TTS engine.

### 2. Retrieval-Augmented Generation
- **Technology**: 
- **Embeddings**: 
- **Workflow**:
  - **Ingestion**: 
  - **Metadata Tagging**: Documents are tagged as either `Product` (policies) or `Regulatory` (annual reports).
  - **Vector Storage**: 
  - **Retrieval**: 

### 3. Language Processing 
- **Technology**: 
- **Role**: The "reasoning" engine.
- **System Prompting**: Enforces a persona of a "Professional Insurance Advisor." It is explicitly instructed to:
  - Compare multiple policies.
  - Use specific citation tags (e.g., `[Source: ...]`).
  - Be concise for voice-based interactions.

### 4. Voice Pipeline 
- **STT (Speech-to-Text)**:
- **TTS (Text-to-Speech)**:

---

## How It Works (Step-by-Step)

1.  **Document Indexing**: User uploads a PDF. 
2.  **Voice Input**: Captures raw audio.
3.  **Transcription**: Converts the audio bytes into text.
4.  **Contextual Retrieval**: The text query is embedded and matched against the DB. The engine returns the most relevant snippets along with their source.
5.  **Response Generation**: The LLM receives the user's question and the retrieved snippets. It synthesises a natural language answer.
6.  **Voice Synthesis**: The answer is converted to voice
7.  **Playback**: Audio component plays the response automatically

