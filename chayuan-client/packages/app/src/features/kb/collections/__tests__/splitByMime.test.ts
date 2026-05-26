/**
 * 94-4:splitByMime 单测。
 */
import { describe, expect, it } from 'vitest';
import { fileKindFor, splitByMime } from '../splitByMime';

function mkFile(name: string, type = ''): File {
  return new File([new Blob(['x'])], name, { type });
}

describe('fileKindFor', () => {
  it('image extension → image', () => {
    expect(fileKindFor(mkFile('a.jpg'))).toBe('image');
    expect(fileKindFor(mkFile('A.PNG'))).toBe('image');
    expect(fileKindFor(mkFile('photo.webp'))).toBe('image');
  });

  it('document extension → document', () => {
    expect(fileKindFor(mkFile('doc.pdf'))).toBe('document');
    expect(fileKindFor(mkFile('memo.docx'))).toBe('document');
    expect(fileKindFor(mkFile('readme.md'))).toBe('document');
    expect(fileKindFor(mkFile('data.csv'))).toBe('document');
  });

  it('mime takes priority over extension', () => {
    // 有 image/ mime 即使无扩展名也判为 image
    expect(fileKindFor(mkFile('weird', 'image/jpeg'))).toBe('image');
  });

  it('unknown → other', () => {
    expect(fileKindFor(mkFile('a.exe'))).toBe('other');
    expect(fileKindFor(mkFile('a.zip'))).toBe('other');
    expect(fileKindFor(mkFile('noext'))).toBe('other');
  });
});

describe('splitByMime', () => {
  it('partitions mixed list', () => {
    const r = splitByMime([
      mkFile('a.pdf'), mkFile('b.jpg'), mkFile('c.docx'),
      mkFile('d.png'), mkFile('e.exe'),
    ]);
    expect(r.documents.map((f) => f.name)).toEqual(['a.pdf', 'c.docx']);
    expect(r.images.map((f) => f.name)).toEqual(['b.jpg', 'd.png']);
    expect(r.other.map((f) => f.name)).toEqual(['e.exe']);
  });

  it('handles FileList-like', () => {
    const files = [mkFile('a.pdf'), mkFile('b.png')];
    const r = splitByMime(files);
    expect(r.documents).toHaveLength(1);
    expect(r.images).toHaveLength(1);
  });

  it('empty input returns empty splits', () => {
    const r = splitByMime([]);
    expect(r.documents).toEqual([]);
    expect(r.images).toEqual([]);
    expect(r.other).toEqual([]);
  });
});
