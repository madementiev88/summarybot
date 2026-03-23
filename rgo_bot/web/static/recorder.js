/**
 * MediaRecorder wrapper for audio recording in Telegram Mini App.
 * Records audio as webm/opus, returns Blob on stop.
 */

class AudioRecorder {
  constructor() {
    this.mediaRecorder = null;
    this.chunks = [];
    this.stream = null;
    this.startTime = null;
    this.timerInterval = null;
    this.onTick = null; // callback(seconds)
  }

  /**
   * Start recording audio from microphone.
   * @param {Function} onTick - callback called every second with elapsed seconds
   * @returns {Promise<void>}
   */
  async start(onTick) {
    this.onTick = onTick;
    this.chunks = [];

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          sampleRate: 16000,
        },
      });
    } catch (err) {
      throw new Error('Нет доступа к микрофону');
    }

    // Prefer webm/opus, fallback to whatever is available
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : '';

    const options = mimeType ? { mimeType } : {};
    this.mediaRecorder = new MediaRecorder(this.stream, options);

    this.mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) {
        this.chunks.push(e.data);
      }
    };

    this.mediaRecorder.start(1000); // collect data every second
    this.startTime = Date.now();

    // Timer
    if (onTick) {
      this.timerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
        onTick(elapsed);
      }, 1000);
    }
  }

  /**
   * Stop recording and return the audio blob.
   * @returns {Promise<Blob>}
   */
  stop() {
    return new Promise((resolve) => {
      if (this.timerInterval) {
        clearInterval(this.timerInterval);
        this.timerInterval = null;
      }

      if (!this.mediaRecorder || this.mediaRecorder.state === 'inactive') {
        resolve(null);
        return;
      }

      this.mediaRecorder.onstop = () => {
        const blob = new Blob(this.chunks, {
          type: this.mediaRecorder.mimeType || 'audio/webm',
        });
        this.chunks = [];

        // Stop all tracks
        if (this.stream) {
          this.stream.getTracks().forEach((t) => t.stop());
          this.stream = null;
        }

        resolve(blob);
      };

      this.mediaRecorder.stop();
    });
  }

  /** Check if currently recording */
  get isRecording() {
    return this.mediaRecorder && this.mediaRecorder.state === 'recording';
  }

  /** Get elapsed seconds */
  get elapsed() {
    if (!this.startTime) return 0;
    return Math.floor((Date.now() - this.startTime) / 1000);
  }
}
