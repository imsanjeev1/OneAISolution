const menuItems = Array.from(document.querySelectorAll('.menu-item'));
const panels = Array.from(document.querySelectorAll('.panel'));
const taskForms = Array.from(document.querySelectorAll('.task-form[data-endpoint]'));

const chatbotState = {
    collectionId: '',
    history: [],
    collections: [],
    source: 'rag',
};

function getChatbotElements() {
    return {
        collectionField: document.getElementById('chatbot-collection'),
        collectionSelect: document.getElementById('chatbot-collection-select'),
        log: document.getElementById('chat-log'),
        sources: document.getElementById('chat-sources'),
        status: document.getElementById('chatbot-upload-status'),
    };
}

function resetChatLog(message) {
    const { log, sources } = getChatbotElements();
    log.innerHTML = '';
    appendMessage('assistant', message);
    sources.textContent = '';
}

function setActiveChatbotCollection(collectionId, message) {
    const { collectionField, collectionSelect, status } = getChatbotElements();
    const collection = chatbotState.collections.find((item) => item.collection_id === collectionId);

    chatbotState.collectionId = collectionId;
    chatbotState.history = [];
    collectionField.value = collectionId || '';
    collectionSelect.value = collectionId || '';

    if (!collectionId) {
        status.classList.remove('error');
        status.textContent = message || 'Choose a saved indexed document or upload a new one.';
        resetChatLog('Select a saved indexed document or upload a new one, then ask questions about its content.');
        return;
    }

    status.classList.remove('error');
    status.textContent = message || `${collection?.source || collectionId} is ready for chat.`;
    resetChatLog(`Using ${collection?.source || collectionId}. Ask questions only from the indexed content.`);
}

function getChatbotSource() {
    return chatbotState.source;
}

function isRagSource() {
    return getChatbotSource() === 'rag';
}

function updateChatbotSourceUI() {
    const uploadForm = document.getElementById('chatbot-upload-form');
    const status = document.getElementById('chatbot-upload-status');
    const source = getChatbotSource();

    uploadForm.classList.toggle('source-disabled', !isRagSource());

    if (!isRagSource()) {
        chatbotState.collectionId = '';
        document.getElementById('chatbot-collection').value = '';
        status.classList.remove('error');
        if (source === 'wiki') {
            status.textContent = 'WIKI is selected. Chat will fetch live results from WIKI_API_URL. WIKI_TOKEN is optional.';
            resetChatLog('WIKI is selected. Ask your question and the chat will answer from retrieved wiki results.');
            return;
        }

        status.textContent = `${source.toUpperCase()} is selected. Chat will fetch live results from the configured ${source} connection.`;
        resetChatLog(`${source.toUpperCase()} is selected. Ask your question and the chat will answer from retrieved ${source} results.`);
        return;
    }

    if (chatbotState.collections.length) {
        setActiveChatbotCollection(chatbotState.collectionId || chatbotState.collections[0].collection_id, 'RAG is selected. Choose a saved indexed document or upload a new one.');
        return;
    }

    setActiveChatbotCollection('', 'RAG is selected. Choose a saved indexed document or upload a new one.');
}

function renderCollectionOptions(collections) {
    const { collectionSelect } = getChatbotElements();
    collectionSelect.innerHTML = '';

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = collections.length ? 'Select a saved indexed document' : 'No indexed documents available';
    collectionSelect.appendChild(placeholder);

    collections.forEach((collection) => {
        const option = document.createElement('option');
        option.value = collection.collection_id;
        option.textContent = `${collection.source} (${collection.chunks_indexed} chunks)`;
        collectionSelect.appendChild(option);
    });
}

async function loadChatbotCollections(preferredCollectionId = '') {
    const { status } = getChatbotElements();
    status.classList.remove('error');
    status.textContent = 'Loading indexed documents...';

    try {
        const response = await fetch('/api/chatbot/collections');
        if (!response.ok) {
            throw new Error(await parseError(response));
        }

        const collections = await response.json();
        chatbotState.collections = collections;
        renderCollectionOptions(collections);

        if (!isRagSource()) {
            updateChatbotSourceUI();
            return;
        }

        const selectedCollectionId = preferredCollectionId
            || chatbotState.collectionId
            || collections[0]?.collection_id
            || '';

        if (selectedCollectionId) {
            const selectedCollection = collections.find((item) => item.collection_id === selectedCollectionId);
            setActiveChatbotCollection(
                selectedCollectionId,
                selectedCollection
                    ? `${selectedCollection.source} is ready for chat.`
                    : 'Indexed document is ready for chat.',
            );
            return;
        }

        setActiveChatbotCollection('', 'No indexed documents available. Upload a document to start.');
    } catch (error) {
        status.classList.add('error');
        status.textContent = error.message;
        renderCollectionOptions([]);
        setActiveChatbotCollection('', 'Unable to load indexed documents. Upload a document to start.');
    }
}

function setActivePanel(targetId) {
    menuItems.forEach((item) => item.classList.toggle('active', item.dataset.target === targetId));
    panels.forEach((panel) => panel.classList.toggle('active', panel.id === targetId));
}

function setBusy(button, busy, label) {
    if (!button) {
        return;
    }
    if (!button.dataset.defaultLabel) {
        button.dataset.defaultLabel = button.textContent;
    }
    button.disabled = busy;
    button.textContent = busy ? label : button.dataset.defaultLabel;
}

async function parseError(response) {
    try {
        const payload = await response.json();
        return payload.detail || 'Request failed.';
    } catch {
        return 'Request failed.';
    }
}

async function handleTaskFormSubmit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const output = form.querySelector('.output');
    const textInput = form.querySelector('[name="text"]');

    setBusy(button, true, 'Running...');
    output.value = 'Processing...';

    try {
        const response = await fetch(form.dataset.endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: textInput.value.trim() }),
        });
        if (!response.ok) {
            throw new Error(await parseError(response));
        }
        const payload = await response.json();
        output.value = payload.result || 'No response returned.';
    } catch (error) {
        output.value = error.message;
    } finally {
        setBusy(button, false);
    }
}

async function handleVisionSubmit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const output = document.getElementById('vision-output');
    const formData = new FormData(form);

    setBusy(button, true, 'Analyzing...');
    output.value = 'Uploading image and requesting analysis...';

    try {
        const response = await fetch('/api/text-to-image', {
            method: 'POST',
            body: formData,
        });
        if (!response.ok) {
            throw new Error(await parseError(response));
        }
        const payload = await response.json();
        output.value = payload.result || 'No response returned.';
    } catch (error) {
        output.value = error.message;
    } finally {
        setBusy(button, false);
    }
}

function appendMessage(role, text) {
    const log = document.getElementById('chat-log');
    const article = document.createElement('article');
    article.className = `message ${role}`;
    const paragraph = document.createElement('p');
    paragraph.textContent = text;
    article.appendChild(paragraph);
    log.appendChild(article);
    log.scrollTop = log.scrollHeight;
}

async function handleChatbotUpload(event) {
    event.preventDefault();
    if (!isRagSource()) {
        return;
    }
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const status = document.getElementById('chatbot-upload-status');
    const collectionField = document.getElementById('chatbot-collection');
    const formData = new FormData(form);

    setBusy(button, true, 'Indexing...');
    status.classList.remove('error');
    status.textContent = 'Uploading and indexing document into Chroma DB...';

    try {
        const response = await fetch('/api/chatbot/upload', {
            method: 'POST',
            body: formData,
        });
        if (!response.ok) {
            throw new Error(await parseError(response));
        }

        const payload = await response.json();
        collectionField.value = payload.collection_id;
        await loadChatbotCollections(payload.collection_id);
        status.textContent = `${payload.filename} indexed into ${payload.collection_id} with ${payload.chunks_indexed} chunks and is ready for chat.`;
    } catch (error) {
        status.classList.add('error');
        status.textContent = error.message;
    } finally {
        setBusy(button, false);
    }
}

async function handleChatSubmit(event) {
    event.preventDefault();
    const input = document.getElementById('chat-question');
    const button = event.currentTarget.querySelector('button[type="submit"]');
    const sources = document.getElementById('chat-sources');
    const question = input.value.trim();

    if (isRagSource() && !chatbotState.collectionId) {
        sources.classList.add('error');
        sources.textContent = chatbotState.collections.length
            ? 'Select a saved indexed document before starting the chat.'
            : 'No indexed document is available yet. Upload one document to start.';
        return;
    }
    if (!question) {
        return;
    }

    sources.classList.remove('error');
    appendMessage('user', question);
    input.value = '';
    setBusy(button, true, 'Sending...');

    try {
        const response = await fetch('/api/chatbot/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                collection_id: chatbotState.collectionId,
                source: chatbotState.source,
                question,
                history: chatbotState.history,
            }),
        });
        if (!response.ok) {
            throw new Error(await parseError(response));
        }

        const payload = await response.json();
        appendMessage('assistant', payload.answer);
        chatbotState.history.push({ role: 'user', content: question });
        chatbotState.history.push({ role: 'assistant', content: payload.answer });
        sources.textContent = payload.sources.length ? `Sources: ${payload.sources.join(', ')}` : '';
    } catch (error) {
        appendMessage('assistant', error.message);
        sources.classList.add('error');
        sources.textContent = error.message;
    } finally {
        setBusy(button, false);
    }
}

function handleChatbotSourceChange(event) {
    chatbotState.source = event.currentTarget.value;
    chatbotState.history = [];
    updateChatbotSourceUI();
}

function handleChatbotCollectionChange(event) {
    const collectionId = event.currentTarget.value;
    if (!collectionId) {
        setActiveChatbotCollection('', chatbotState.collections.length
            ? 'Select a saved indexed document or upload a new one.'
            : 'No indexed documents available. Upload a document to start.');
        return;
    }

    const collection = chatbotState.collections.find((item) => item.collection_id === collectionId);
    setActiveChatbotCollection(collectionId, `${collection?.source || collectionId} is ready for chat.`);
}

menuItems.forEach((item) => {
    item.addEventListener('click', () => {
        setActivePanel(item.dataset.target);
        if (item.dataset.target === 'chatbotai') {
            loadChatbotCollections();
        }
    });
});

taskForms.forEach((form) => form.addEventListener('submit', handleTaskFormSubmit));
document.getElementById('vision-form').addEventListener('submit', handleVisionSubmit);
document.getElementById('chatbot-upload-form').addEventListener('submit', handleChatbotUpload);
document.getElementById('chat-form').addEventListener('submit', handleChatSubmit);
document.getElementById('chatbot-collection-select').addEventListener('change', handleChatbotCollectionChange);
document.getElementById('chatbot-refresh-collections').addEventListener('click', () => loadChatbotCollections());
document.querySelectorAll('input[name="chatbot-source"]').forEach((radio) => radio.addEventListener('change', handleChatbotSourceChange));

loadChatbotCollections();
