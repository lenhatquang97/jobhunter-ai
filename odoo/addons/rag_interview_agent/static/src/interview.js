// Candidate page. Voice runs over WebRTC straight to OpenAI Realtime (speech-to-speech); Odoo's RAG agent decides
// every word the voice says. Realtime answers nothing on its own (create_response=false on the server side).
(() => {
    const token = location.pathname.split('/').pop();
    const $ = (id) => document.getElementById(id);
    const rpc = async (path, params = {}) => {
        const r = await fetch(`/rag-interview/${token}/${path}`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({jsonrpc: '2.0', method: 'call', params}),
        });
        const body = r.ok ? await r.json() : null;
        return (body && body.result) || {error: 'ai'};
    };
    const status = (text, mode = '') => { $('status').textContent = text; $('orb').className = 'orb ' + mode; };

    let pc, dc, answer = '', speaking = false, finished = false, busy = false, speechTimer;
    const submitAfterPause = () => { clearTimeout(speechTimer); speechTimer = setTimeout(submit, 3500); };

    const say = (text) => new Promise((resolve) => {
        speaking = true; status('Speaking...', 'talk');
        $('question').textContent = text;
        say.done = resolve;
        // Out-of-band, empty input: the voice sees only this text, never the candidate's audio, so it cannot drift
        // into the candidate's language.
        dc.send(JSON.stringify({type: 'response.create', response: {
            conversation: 'none', input: [], output_modalities: ['audio'],
            instructions: 'Speak English only, with a natural American English accent. Read the following text aloud '
                + `exactly as written, word for word, and add nothing:
${text}`,
        }}));
    });
    // Audio keeps playing a moment after the response is generated: wait for the buffer to drain.
    const spoke = () => { speaking = false; dc.send(JSON.stringify({type: 'input_audio_buffer.clear'}));
                          if (say.done) { say.done(); say.done = null; } };

    const listen = () => {
        answer = ''; $('heard').textContent = '';
        status('Your turn, I am listening...', 'listen');
        $('done').disabled = false;
    };

    const handle = async (res) => {
        if (res.error === 'closed') return end('The interview has ended.');
        if (res.error === 'busy') return;
        if (res.error) {
            await say('Sorry, the connection dropped. Could you please repeat your answer?');
            return listen();
        }
        await say(res.question);
        if (res.done) return end('Thank you! The interview is over, you can close this page.');
        listen();
    };

    const submit = async () => {
        const text = answer.trim();
        if (busy || speaking || finished || !text) return;
        busy = true; $('done').disabled = true;
        status('Thinking...');
        try { await handle(await rpc('answer', {text})); } finally { busy = false; }
    };
    $('done').addEventListener('click', submit);

    const onEvent = (e) => {
        const ev = JSON.parse(e.data);
        if (ev.type === 'output_audio_buffer.stopped' || ev.type === 'output_audio_buffer.cleared') spoke();
        else if (ev.type === 'conversation.item.input_audio_transcription.completed' && !speaking && !busy && !finished) {
            // server VAD closed a speech segment; one answer can span several segments, so pause-to-send is ours
            answer += (ev.transcript || '') + ' ';
            $('heard').textContent = answer.trim();
            submitAfterPause();
        } else if (ev.type === 'error') console.warn('realtime', ev.error);
    };

    const end = (text) => {
        finished = true; $('done').disabled = true; status(text);
        setTimeout(() => { if (pc) pc.close(); }, 1500);
    };

    $('agree').addEventListener('change', (e) => { $('start').disabled = !e.target.checked; });
    $('start').addEventListener('click', async () => {
        $('start').disabled = true; $('blocker').textContent = '';
        let mic;
        try {
            mic = await navigator.mediaDevices.getUserMedia({audio: {
                channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
            }});
        } catch {
            $('blocker').textContent = 'Microphone access is required for the interview.'; $('start').disabled = false; return;
        }
        const voice = await rpc('voice');
        if (voice.error) {
            $('blocker').textContent = voice.error === 'closed' ? 'The interview has ended.' : `Could not connect to the voice service: ${voice.detail || 'please try again.'}`;
            $('start').disabled = false; return;
        }
        pc = new RTCPeerConnection();
        const audio = new Audio(); audio.autoplay = true;
        pc.ontrack = (e) => { audio.srcObject = e.streams[0]; };
        pc.addTrack(mic.getTracks()[0]);
        dc = pc.createDataChannel('oai-events');
        dc.onmessage = onEvent;
        await pc.setLocalDescription(await pc.createOffer());
        const sdp = await fetch(`https://api.openai.com/v1/realtime/calls?model=${encodeURIComponent(voice.model)}`, {
            method: 'POST', body: pc.localDescription.sdp,
            headers: {Authorization: `Bearer ${voice.secret}`, 'Content-Type': 'application/sdp'},
        });
        if (!sdp.ok) { $('blocker').textContent = 'Could not connect to the voice service.'; $('start').disabled = false; return; }
        await pc.setRemoteDescription({type: 'answer', sdp: await sdp.text()});
        await new Promise((r) => (dc.readyState === 'open' ? r() : dc.addEventListener('open', r, {once: true})));
        $('consent').hidden = true; $('stage').hidden = false;
        status('Preparing the first question...');
        busy = true;
        try { await handle(await rpc('start')); } finally { busy = false; }
    });
})();
