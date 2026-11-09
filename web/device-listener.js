/* Seva device microphone capture.
 *
 * The cloud server cannot hear a visitor's phone. This file captures the
 * phone's own microphone with getUserMedia, resamples to 16 kHz mono PCM,
 * encodes a bounded WAV clip in the browser, and uploads it one clip at a
 * time. The server runs Gnani speech recognition and the shared matcher.
 *
 * It does not claim background listening. Audio stops when the page is
 * closed, the user presses Stop, or an announcement is detected.
 */
(function (global) {
  "use strict";

  var TARGET_RATE = 16000;
  var BLOCK_SIZE = 4096;
  var PRE_ROLL_BLOCKS = 4;
  var RELEASE_BLOCKS = 10;
  var MAX_BUFFER_SECONDS = 7;
  var MIN_BUFFER_SECONDS = 0.7;
  var SILENCE_FLOOR = 0.005;
  var TRIGGER_DB = 12;
  var BACKGROUND_ALPHA = 0.05;
  var POLL_MS = 3000;
  var MAX_QUEUED_CHUNKS = 3;

  function rms(samples) {
    var total = 0;
    for (var i = 0; i < samples.length; i++) {
      total += samples[i] * samples[i];
    }
    return Math.sqrt(total / (samples.length || 1));
  }

  function resampleLinear(input, inputRate, outputRate) {
    if (!input.length || inputRate === outputRate) {
      return new Float32Array(input);
    }
    var ratio = inputRate / outputRate;
    var length = Math.max(1, Math.round(input.length / ratio));
    var out = new Float32Array(length);
    for (var i = 0; i < length; i++) {
      var position = i * ratio;
      var left = Math.floor(position);
      var fraction = position - left;
      var a = input[left] || 0;
      var b = input[left + 1];
      if (b === undefined) b = a;
      out[i] = a + (b - a) * fraction;
    }
    return out;
  }

  function concatFloat32(frames, totalLength) {
    var out = new Float32Array(totalLength);
    var offset = 0;
    for (var i = 0; i < frames.length; i++) {
      out.set(frames[i], offset);
      offset += frames[i].length;
    }
    return out;
  }

  function encodeWav(frames, sampleRate) {
    var total = 0;
    for (var i = 0; i < frames.length; i++) total += frames[i].length;
    if (!total) return null;

    var samples = concatFloat32(frames, total);
    var buffer = new ArrayBuffer(44 + samples.length * 2);
    var view = new DataView(buffer);

    function writeString(offset, text) {
      for (var j = 0; j < text.length; j++) {
        view.setUint8(offset + j, text.charCodeAt(j));
      }
    }

    writeString(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, samples.length * 2, true);

    var offset = 44;
    for (var k = 0; k < samples.length; k++) {
      var value = Math.max(-1, Math.min(1, samples[k]));
      view.setInt16(offset, value < 0 ? value * 0x8000 : value * 0x7FFF, true);
      offset += 2;
    }
    return new Blob([buffer], { type: "audio/wav" });
  }

  function httpError(response, body, fallback) {
    var error = new Error((body && body.error) || fallback);
    error.status = response.status;
    error.code = (body && body.code) || "";
    return error;
  }

  function readJson(response) {
    return response.json().catch(function () { return {}; }).then(function (body) {
      if (!response.ok) throw httpError(response, body, "Request failed");
      return body;
    });
  }

  function micErrorName(error) {
    return (error && error.name) || "";
  }

  function create(options) {
    var config = options || {};
    var sessionId = null;
    var running = false;
    var detected = false;
    var stream = null;
    var context = null;
    var source = null;
    var processor = null;
    var sink = null;
    var pollTimer = null;
    var queue = [];
    var uploading = false;
    var failedPolls = 0;
    var droppedChunks = 0;
    var sentChunks = 0;
    var lastStatus = "";

    var diagRms = 0;
    var diagBackground = SILENCE_FLOOR;
    var diagDb = 0;
    var diagCallbacks = 0;
    var diagLastCallback = 0;
    var diagGateOpen = false;
    var diagFrames = 0;

    var gate = {
      primed: false,
      open: false,
      background: SILENCE_FLOOR,
      quiet: 0,
      pre: [],
      buffer: [],
      buffered: 0
    };

    function emit(status, detail) {
      lastStatus = status;
      if (typeof config.onState === "function") {
        config.onState(status, detail || "");
      }
    }

    function debug(message) {
      if (typeof config.onDebug === "function") config.onDebug(message);
      // Always log critical errors to console for production debugging
      if (message.includes("error") || message.includes("failed") || message.includes("status")) {
        console.log("[SevaDeviceAudio] " + message);
      }
    }

    function stopCapture() {
      if (processor) {
        try { processor.onaudioprocess = null; } catch (e) {}
        try { processor.disconnect(); } catch (e) {}
        processor = null;
      }
      if (source) {
        try { source.disconnect(); } catch (e) {}
        source = null;
      }
      if (sink) {
        try { sink.disconnect(); } catch (e) {}
        sink = null;
      }
      if (stream) {
        try {
          stream.getTracks().forEach(function (track) { track.stop(); });
        } catch (e) {}
        stream = null;
      }
      if (context && context.state !== "closed") {
        try { context.close().catch(function () {}); } catch (e) {}
      }
      context = null;
    }

    function clearSession() {
      sessionId = null;
    }

    function notifyStop() {
      var id = sessionId;
      if (!id) return;
      var payload = JSON.stringify({ session_id: id });
      try {
        if (navigator.sendBeacon) {
          var blob = new Blob([payload], { type: "application/json" });
          if (navigator.sendBeacon(config.stopUrl, blob)) {
            clearSession();
            return;
          }
        }
      } catch (e) {}
      try {
        fetch(config.stopUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: payload,
          keepalive: true
        }).catch(function () {});
      } catch (e) {}
      clearSession();
    }

    function stop(silent) {
      var wasRunning = running;
      running = false;
      detected = false;
      clearInterval(pollTimer);
      pollTimer = null;
      queue = [];
      uploading = false;
      stopCapture();
      if (!silent) {
        notifyStop();
        if (wasRunning) emit("stopped");
      } else {
        clearSession();
      }
    }

    function finishWithAlert(alert) {
      if (!alert || detected) return false;
      if (alert.verdict !== "mine" && alert.verdict !== "missing") return false;
      if (alert.duplicate) return false;
      detected = true;
      emit("detected", "");
      /* The listening screen writes a sanitised public result. Raw
         transcripts stay on the server and out of browser storage. */
      var callback = config.onDetected;
      running = false;
      clearInterval(pollTimer);
      pollTimer = null;
      queue = [];
      stopCapture();
      notifyStop();
      if (typeof callback === "function") callback(alert);
      return true;
    }

    function handleChunkResponse(body) {
      if (body && body.alert) {
        if (finishWithAlert(body.alert)) return;
      }
      if (!running) return;
      var verdict = body && body.verdict;
      if (verdict === "missing_pending") {
        emit("pending_missing", "");
      } else if (verdict === "other") {
        emit("not_detected", "other");
      } else if (verdict === "none" || verdict === "empty") {
        emit("not_detected", "");
      } else if (verdict === "duplicate") {
        emit("duplicate", "");
      } else if (verdict === "mine" || verdict === "missing") {
        emit("detected", "");
      } else {
        emit("listening", "");
      }
    }

    function uploadChunk(blob) {
      var form = new FormData();
      form.append("session_id", sessionId || "");
      form.append("audio", blob, "chunk.wav");
      sentChunks += 1;
      debug("uploading chunk " + sentChunks + " (" + blob.size + " bytes)");
      return fetch(config.chunkUrl, {
        method: "POST",
        body: form,
        credentials: "same-origin"
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          if (response.status === 410) {
            debug("session expired (410)");
            emit("session_expired", "");
            stop(true);
            clearSession();
            return;
          }
          if (response.status === 429) {
            debug("rate limited (429)");
            emit("rate_limited", "");
            return;
          }
          if (!response.ok) {
            var errorMsg = (body && body.error) || "Unknown error";
            var debugHint = (body && body.debug_hint) || "";
            debug("chunk failed: status=" + response.status + " error=" + errorMsg + " hint=" + debugHint);
            if (response.status === 502 || response.status === 503) {
              emit("service_unavailable", errorMsg);
            } else if (response.status === 500) {
              emit("error", "Server error: " + errorMsg);
            } else {
              emit("error", body.error || "The server could not process audio.");
            }
            return;
          }
          debug("chunk " + sentChunks + " processed: verdict=" + (body.verdict || "unknown"));
          failedPolls = 0;
          handleChunkResponse(body);
        });
      }).catch(function (err) {
        if (!running) return;
        failedPolls += 1;
        debug("chunk network error: " + (err.message || "fetch failed"));
        if (failedPolls >= 2) emit("offline", "");
      });
    }

    function drainQueue() {
      if (uploading || !running || !sessionId) return;
      var blob = queue.shift();
      if (!blob) return;
      uploading = true;
      emit("processing", "");
      var done = function () {
        uploading = false;
        drainQueue();
      };
      uploadChunk(blob).then(done, done);
    }

    function enqueue(blob) {
      if (!blob || !running) return;
      if (queue.length >= MAX_QUEUED_CHUNKS) {
        droppedChunks += 1;
        debug("dropped a chunk because the upload queue was full");
        return;
      }
      queue.push(blob);
      drainQueue();
    }

    function flushBuffer() {
      if (gate.buffered < MIN_BUFFER_SECONDS) {
        gate.buffer = [];
        gate.buffered = 0;
        return;
      }
      var blob = encodeWav(gate.buffer, TARGET_RATE);
      gate.buffer = [];
      gate.buffered = 0;
      if (blob) enqueue(blob);
    }

    function processBlock(samples, inputRate) {
      var level = rms(samples);
      diagRms = level;
      diagBackground = gate.background;
      diagDb = gate.primed ? (20 * Math.log10(Math.max(level, 1e-6) / Math.max(gate.background, 1e-6))) : 0;
      diagCallbacks += 1;
      diagLastCallback = Date.now();
      diagGateOpen = gate.open;
      diagFrames += samples.length;

      var resampled = resampleLinear(samples, inputRate, TARGET_RATE);

      if (!gate.primed) {
        gate.background = Math.max(SILENCE_FLOOR, level);
        gate.primed = true;
        emit("listening");
        return;
      }

      var loud = level >= SILENCE_FLOOR &&
        (20 * Math.log10(level / gate.background)) >= TRIGGER_DB;

      if (!gate.open) {
        gate.background = (1 - BACKGROUND_ALPHA) * gate.background +
          BACKGROUND_ALPHA * level;
        if (gate.background < SILENCE_FLOOR) gate.background = SILENCE_FLOOR;
        gate.pre.push(resampled);
        while (gate.pre.length > PRE_ROLL_BLOCKS) gate.pre.shift();
        if (!loud) return;

        gate.open = true;
        gate.quiet = 0;
        gate.buffer = gate.pre.slice();
        gate.buffered = 0;
        for (var i = 0; i < gate.buffer.length; i++) {
          gate.buffered += gate.buffer[i].length / TARGET_RATE;
        }
        gate.pre = [];
        emit("speech");
      } else {
        gate.buffer.push(resampled);
        gate.buffered += resampled.length / TARGET_RATE;
        if (loud) {
          gate.quiet = 0;
        } else {
          gate.quiet += 1;
        }
      }

      if (gate.open && gate.buffered >= MAX_BUFFER_SECONDS) {
        flushBuffer();
      }
      if (gate.open && gate.quiet >= RELEASE_BLOCKS) {
        gate.open = false;
        gate.quiet = 0;
        flushBuffer();
      }
    }

    function startAudio() {
      var AudioContextClass = global.AudioContext || global.webkitAudioContext;
      if (!AudioContextClass) {
        emit("unsupported", "");
        return Promise.reject(new Error("AudioContext is not available"));
      }
      if (global.isSecureContext === false) {
        emit("insecure", "");
        return Promise.reject(new Error("Microphone requires HTTPS"));
      }
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        emit("unsupported", "");
        return Promise.reject(new Error("getUserMedia is not available"));
      }

      emit("requesting_permission", "");
      return navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      }).then(function (mediaStream) {
        if (!running) {
          try { mediaStream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) {}
          return null;
        }
        stream = mediaStream;
        context = new AudioContextClass();
        source = context.createMediaStreamSource(stream);
        processor = context.createScriptProcessor(BLOCK_SIZE, 1, 1);
        sink = context.createGain();
        sink.gain.value = 0;
        var inputRate = context.sampleRate;
        processor.onaudioprocess = function (event) {
          if (!running) return;
          processBlock(event.inputBuffer.getChannelData(0), inputRate);
        };
        source.connect(processor);
        processor.connect(sink);
        sink.connect(context.destination);

        if (context.state === "suspended") {
          /* Chrome may keep an AudioContext suspended until a gesture. A tap
             anywhere on the screen resumes it; we do not stay stuck waiting. */
          emit("tap_to_start", "");
          context.resume().then(function () {
            if (running && context && context.state === "running") {
              emit("listening");
            }
          }).catch(function () {});
          var resumeOnTap = function () {
            if (context && context.state === "suspended") {
              context.resume().then(function () {
                if (running) emit("listening");
              }).catch(function () {});
            }
          };
          document.addEventListener("pointerdown", resumeOnTap, { once: true });
        } else {
          emit("listening");
        }
        return null;
      }).catch(function (error) {
        var name = micErrorName(error);
        if (name === "NotAllowedError" || name === "PermissionDeniedError") {
          emit("permission_denied", "");
        } else if (name === "NotFoundError" || name === "DevicesNotFoundError") {
          emit("no_microphone", "");
        } else if (name === "NotReadableError" || name === "TrackStartError") {
          emit("mic_error", "");
        } else if (name === "SecurityError") {
          emit("insecure", "");
        } else if (name === "OverconstrainedError") {
          emit("mic_error", "");
        } else if (running) {
          emit("mic_error", "");
        }
        throw error;
      });
    }

    function createSession() {
      debug("creating session: kind=" + (config.kind || "hospital") + " value=" + (config.value || "") + " lang=" + (config.language || "en-IN"));
      return fetch(config.sessionUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          kind: config.kind || "hospital",
          value: config.value || "",
          language: config.language || "en-IN"
        })
      }).then(readJson).then(function (body) {
        if (!body.session_id) throw new Error("The server did not create a listening session.");
        sessionId = body.session_id;
        debug("session created: " + sessionId.substring(0, 12) + "...");
        return body;
      }).catch(function (err) {
        debug("session creation failed: " + (err.message || "unknown error"));
        throw err;
      });
    }

    function pollState() {
      if (!running || !sessionId) return;
      fetch(config.stateUrl + "?session_id=" + encodeURIComponent(sessionId), {
        credentials: "same-origin"
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          if (response.status === 410) {
            emit("session_expired", "");
            stop(true);
            clearSession();
            return;
          }
          if (!response.ok) throw new Error("state failed");
          failedPolls = 0;
          if (body.alert) finishWithAlert(body.alert);
        });
      }).catch(function () {
        if (!running) return;
        failedPolls += 1;
        if (failedPolls >= 3) emit("offline", "");
      });
    }

    function start() {
      if (running) return Promise.resolve();
      detected = false;
      lastStatus = "";
      running = true;
      failedPolls = 0;
      droppedChunks = 0;
      sentChunks = 0;
      queue = [];
      uploading = false;
      gate = {
        primed: false,
        open: false,
        background: SILENCE_FLOOR,
        quiet: 0,
        pre: [],
        buffer: [],
        buffered: 0
      };

      return createSession().then(function () {
        return startAudio();
      }).then(function () {
        if (!running) return;
        pollTimer = setInterval(pollState, POLL_MS);
      }).catch(function (error) {
        if (!running && lastStatus !== "permission_denied" &&
            lastStatus !== "no_microphone" && lastStatus !== "mic_error" &&
            lastStatus !== "insecure" && lastStatus !== "unsupported") {
          emit("unavailable", "");
        }
        if (running) {
          running = false;
          stopCapture();
          notifyStop();
        }
        if (lastStatus === "" || lastStatus === "requesting_permission") {
          emit("unavailable", "");
        }
        debug("start failed: " + ((error && error.message) || ""));
      });
    }

    return {
      start: start,
      stop: stop,
      isRunning: function () { return running; },
      sessionId: function () { return sessionId; },
      stats: function () {
        return { sent: sentChunks, dropped: droppedChunks, queued: queue.length };
      },
      diagnostics: function () {
        return {
          rms: diagRms,
          background: diagBackground,
          db: diagDb,
          callbacksRunning: diagCallbacks > 0 && (Date.now() - diagLastCallback < 2000),
          callbacksCount: diagCallbacks,
          gateOpen: diagGateOpen,
          framesCaptured: diagFrames
        };
      }
    };
  }

  global.SevaDeviceAudio = {
    create: create,
    rms: rms,
    resampleLinear: resampleLinear,
    encodeWav: encodeWav
  };
})(window);
