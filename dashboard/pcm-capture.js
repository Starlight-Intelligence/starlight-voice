// 20 ms mono chunks, transferred without cloning. The client gates submission
// until session.updated and drops capture while muted or stopped.
class StarlightPcmCapture extends AudioWorkletProcessor {
  constructor() { super(); this.chunk = new Float32Array(960); this.offset = 0; }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (input) {
      for (const sample of input) {
        this.chunk[this.offset++] = sample;
        if (this.offset === this.chunk.length) {
          this.port.postMessage(this.chunk, [this.chunk.buffer]);
          this.chunk = new Float32Array(960); this.offset = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("starlight-pcm-capture", StarlightPcmCapture);
