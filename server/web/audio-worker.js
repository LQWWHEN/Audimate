class StereoPackets extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.width = options.processorOptions.bits / 8;
    this.scale = 2 ** (this.width * 8 - 1);
    this.packetBytes = Math.round(sampleRate / 5) * 2 * this.width;
    this.packet = new DataView(new ArrayBuffer(this.packetBytes));
    this.pos = 0;
  }
  process(inputs) {
    const channels = inputs[0];
    if (!channels.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      for (let ch = 0; ch < 2; ch++) {
        const value = (channels[ch] || channels[0])[i];
        const sample = Math.max(
          -this.scale,
          Math.min(this.scale - 1, Math.round(value * this.scale)),
        );
        if (this.width === 4) this.packet.setInt32(this.pos, sample, true);
        else this.packet.setUint16(this.pos, sample & 0xffff, true);
        if (this.width === 3) this.packet.setInt8(this.pos + 2, sample >> 16);
        this.pos += this.width;
      }
      if (this.pos === this.packetBytes) {
        const startTime = (currentFrame+i+1-this.packetBytes/(2*this.width))/sampleRate;
        this.port.postMessage({pcm:this.packet.buffer,startTime}, [this.packet.buffer]);
        this.packet = new DataView(new ArrayBuffer(this.packetBytes));
        this.pos = 0;
      }
    }
    return true;
  }
}
registerProcessor("stereo-packets", StereoPackets);
