// Base64 de um Blob, para mandar bytes dentro de um JSON (genérico).
//
// Uso na nutrição L6 (foto do rótulo): a foto reduzida vai no corpo como base64
// SEM padding. O WAF da API (CRS bloqueando XSS/LFI/RFI/SSRF no corpo desde o
// #579) lê o corpo como texto; bytes de JPEG crus num multipart podem casar uma
// assinatura por acaso, e o alfabeto `[A-Za-z0-9+/]` não forma nenhuma (o `=` do
// padding também fica de fora). A API (`label_photo.decode_upload`) aceita com ou
// sem padding.

/** Bytes por fatia na conversão para string binária (evita estourar a pilha do `apply`). */
const SLICE_BYTES = 0x8000;

export async function blobToBase64(blob: Blob, options: { padding: boolean }): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binario = '';
  for (let inicio = 0; inicio < bytes.length; inicio += SLICE_BYTES) {
    binario += String.fromCharCode(...bytes.subarray(inicio, inicio + SLICE_BYTES));
  }
  const texto = btoa(binario);
  return options.padding ? texto : texto.replace(/=+$/, '');
}
