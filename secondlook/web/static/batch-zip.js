// The receipt bundle for /batch (DAT-850): a plain zip, built in the page from the bytes the server sent, so
// nothing is fetched or kept. Entries are stored, not compressed: receipts are small, and stored entries keep
// each file byte for byte as `secondlook check --receipt` writes it. Every entry is dated 1 Jan 1980, so the
// same receipts always give the same zip.
(function (root) {
  const TABLE = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
    TABLE[n] = c >>> 0;
  }
  function crc32(bytes) {
    let c = 0xFFFFFFFF;
    for (let i = 0; i < bytes.length; i++) c = TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }
  const DOS_DATE = (0 << 9) | (1 << 5) | 1;  // 1980-01-01, the zip format's first date
  const UTF8_NAMES = 0x0800;

  // files: [{ name: "receipts/0001-a.jpg.receipt.json", text: "..." }] -> Uint8Array of the zip.
  function zip(files) {
    const enc = new TextEncoder();
    const parts = [], central = [];
    let offset = 0;
    for (const f of files) {
      const name = enc.encode(f.name), data = f.bytes || enc.encode(f.text);
      const crc = crc32(data);
      const local = new DataView(new ArrayBuffer(30));
      local.setUint32(0, 0x04034B50, true); local.setUint16(4, 20, true); local.setUint16(6, UTF8_NAMES, true);
      local.setUint16(8, 0, true); local.setUint16(10, 0, true); local.setUint16(12, DOS_DATE, true);
      local.setUint32(14, crc, true); local.setUint32(18, data.length, true); local.setUint32(22, data.length, true);
      local.setUint16(26, name.length, true); local.setUint16(28, 0, true);
      parts.push(new Uint8Array(local.buffer), name, data);
      const cd = new DataView(new ArrayBuffer(46));
      cd.setUint32(0, 0x02014B50, true); cd.setUint16(4, 20, true); cd.setUint16(6, 20, true);
      cd.setUint16(8, UTF8_NAMES, true); cd.setUint16(10, 0, true); cd.setUint16(12, 0, true);
      cd.setUint16(14, DOS_DATE, true); cd.setUint32(16, crc, true); cd.setUint32(20, data.length, true);
      cd.setUint32(24, data.length, true); cd.setUint16(28, name.length, true);
      cd.setUint32(42, offset, true);
      central.push(new Uint8Array(cd.buffer), name);
      offset += 30 + name.length + data.length;
    }
    const cdSize = central.reduce((n, p) => n + p.length, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054B50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
    end.setUint32(12, cdSize, true); end.setUint32(16, offset, true);
    const all = parts.concat(central, [new Uint8Array(end.buffer)]);
    const out = new Uint8Array(all.reduce((n, p) => n + p.length, 0));
    let at = 0;
    for (const p of all) { out.set(p, at); at += p.length; }
    return out;
  }

  root.SecondLookZip = { zip, crc32 };
})(typeof window !== "undefined" ? window : globalThis);
