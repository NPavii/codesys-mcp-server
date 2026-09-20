import PDFDocument from "pdfkit";

export async function createSimplePdf(text: string): Promise<Buffer> {
  const doc = new PDFDocument({ autoFirstPage: true });
  const chunks: Buffer[] = [];

  doc.on("data", (chunk: Buffer) => {
    chunks.push(chunk);
  });

  const finished = new Promise<Buffer>((resolve, reject) => {
    doc.on("end", () => resolve(Buffer.concat(chunks)));
    doc.on("error", reject);
  });

  doc.fontSize(12).text(text);
  doc.end();

  return finished;
}
