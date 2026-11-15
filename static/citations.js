/*
 * In-text citations and the reference list.
 *
 * buildCitationIndex(segments) reads the reference list of a paper (rows of
 * kind "citation" and the rows after a "References" heading) and
 * findCitations(text, index) finds the citations in a sentence that point to
 * it. Supported styles:
 *   [12]  [2, 34, 49]  [9–11]        numbered "[n] Author..." or "n. Author..."
 *   [Finn 17a]  [James 18, Todorov 12]   labelled "[Finn 17a] Finn, C., ..."
 *   [Dai et al. 2018; Hu et al. 2015]  (Smith et al., 2020)  Dai et al. [2018]
 *                                     author-year, searched in the list
 */
(function (root) {
  const REFERENCES_HEADING = /^(?:[0-9IVX]+\.?\s*)?(?:references?|bibliography|literature cited|works cited|参考文献|引用文献|文献)\s*$/i;
  const LETTER = '\\p{L}';
  const NAME = `[\\p{Lu}][${LETTER}'’\\-]+`;
  const YEAR = /\b(1[89]\d{2}|20\d{2})([a-z])?\b/g;

  function plain(value) {
    // Script markup E_{x} and inline math tokens are not part of the text.
    return String(value ?? '').replace(/[_^]\{([^{}]*)\}/g, '$1');
  }

  function referenceArea(segments) {
    const rows = [];
    let start = -1;
    let inline = null;
    segments.forEach((item, position) => {
      const value = plain(item.source);
      if (REFERENCES_HEADING.test(value.trim())) {
        start = position;
        inline = null;
        return;
      }
      // "... is published. ♢ 参考文献 ♢ [Abbeel 04] ...": the heading ran
      // into the sentence before it.
      const match = value.match(/(?:♢|◇|■|●)?\s*(?:参考文献|引用文献|References|REFERENCES|Bibliography)\s*(?:♢|◇|■|●)?\s*(?=\[[^\]]{1,30}\]|1\.\s)/);
      if (match) {
        start = position;
        inline = {index: item.index, source: value.slice(match.index + match[0].length)};
      }
    });
    segments.forEach((item, position) => {
      const kind = item.kind || 'sentence';
      if (inline && item.index === inline.index) rows.push({...item, source: inline.source});
      else if (kind === 'citation') rows.push(item);
      else if (start >= 0 && position > start && !['heading', 'figure', 'table', 'equation', 'algorithm'].includes(kind)) {
        // The list ends at the next heading (an appendix).
        const heading = segments.slice(start + 1, position).some((other) => (other.kind || '') === 'heading');
        if (!heading) rows.push(item);
      }
    });
    const seen = new Set();
    let text = '';
    const spans = [];
    rows
      .filter((item) => !seen.has(item.index) && seen.add(item.index))
      .sort((left, right) => Number(left.index) - Number(right.index))
      .forEach((item) => {
        const value = plain(item.source);
        spans.push({start: text.length, end: text.length + value.length, index: Number(item.index)});
        text += `${value}\n`;
      });
    return {text, spans};
  }

  function rowAt(area, position) {
    return area.spans.find((span) => span.start <= position && position <= span.end) || area.spans[area.spans.length - 1];
  }

  function normalLabel(label) {
    return String(label).toLowerCase().replace(/\s+/g, ' ').trim();
  }

  function buildCitationIndex(segments) {
    const area = referenceArea(Array.isArray(segments) ? segments : []);
    const labels = new Map();
    const index = {area, labels, cache: new Map()};
    if (!area.text) return index;
    // "[12] Author" or "[Finn 17a] Finn, C." (a label without a digit, such
    // as "[cs.CV]" inside an entry, is not one).
    const marks = [];
    for (const match of area.text.matchAll(/(^|[\s.])\[([^\[\]\n]{1,30})\](?=\s+\S)/g)) {
      if (!/\d/.test(match[2])) continue;
      marks.push({label: match[2], start: match.index + match[1].length, body: match.index + match[0].length});
    }
    if (marks.length < 2) {
      // "1. Author, A." numbered in order.
      marks.length = 0;
      let expected = 1;
      for (const match of area.text.matchAll(/(^|\s)(\d{1,3})\.\s+(?=[\p{Lu}])/gu)) {
        if (Number(match[2]) !== expected) continue;
        marks.push({label: match[2], start: match.index + match[1].length, body: match.index + match[0].length});
        expected += 1;
      }
      if (marks.length < 3) marks.length = 0;
    }
    marks.forEach((mark, position) => {
      const end = position + 1 < marks.length ? marks[position + 1].start : area.text.length;
      const text = area.text.slice(mark.body, Math.min(end, mark.body + 700)).replace(/\s+/g, ' ').trim();
      const key = normalLabel(mark.label);
      if (!labels.has(key)) labels.set(key, {index: rowAt(area, mark.start).index, label: mark.label, text});
    });
    return index;
  }

  function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  // Ends of entries: after a URL or DOI, or after "15 pages." / "235–243.".
  function boundaries(area) {
    if (!area.boundaries) {
      area.boundaries = [...area.text.matchAll(/(?:https?:\/\/\S+|doi:\s*\S+|\b\d+\s*pages\.|\b\d+\s*[–-]\s*\d+\.)\s+/gi)].map(
        (match) => match.index + match[0].length,
      );
    }
    return area.boundaries;
  }

  function authorEntry(index, surname, year) {
    const key = `${surname}|${year}`;
    if (index.cache.has(key)) return index.cache.get(key);
    const area = index.area;
    let found = null;
    const pattern = new RegExp(`(?<![${LETTER}])${escapeRegExp(surname)}(?![${LETTER}])`, 'gu');
    const candidates = [];
    for (const match of area.text.matchAll(pattern)) {
      const after = area.text.slice(match.index, match.index + 320);
      // The entry's year is the first year after the author: a later one
      // belongs to the next entry.
      const firstYear = after.match(/\b(?:1[89]|20)\d{2}[a-z]?(?![0-9])/);
      if (!firstYear || firstYear[0] !== year) {
        if (!firstYear || firstYear[0].slice(0, 4) !== year.slice(0, 4) || year.length > 4) continue;
      }
      const yearAt = firstYear.index;
      const row = rowAt(area, match.index);
      const boundary = Math.max(row.start, ...boundaries(area).filter((value) => value <= match.index));
      // Back over first names and initials ("Charlie C. L. ").
      const before = area.text.slice(Math.max(boundary, match.index - 48), match.index);
      const names = before.match(/(?:[\p{Lu}][\p{L}'’-]*\.?\s+)+$/u);
      const start = names ? match.index - names[0].length : match.index;
      const first = start - boundary <= 4 || match.index - boundary < 60;
      candidates.push({match, start: first ? Math.min(start, match.index) : start, yearAt: match.index + yearAt, first, row});
    }
    const best = candidates.find((candidate) => candidate.first) || candidates[0];
    if (best) {
      const nextBoundary = boundaries(area).find((value) => value > best.yearAt) ?? Infinity;
      // An entry can continue on the next row; the next entry starts with
      // its author list and ". 2016." after it.
      const rest = area.text.slice(best.yearAt + 6, best.yearAt + 420);
      // Initials ("Niloy J. Mitra", "C.L. Wang") may sit in the author list.
      const nextEntry = rest.search(
        /\.\s+(?=(?:[\p{Lu}][\p{L}'’-]*\.?\s+){0,4}[\p{Lu}][\p{L}'’-]+(?:(?:,|\s+and\s)(?:[^.]|(?<=[\s.\-]\p{Lu})\.){0,240}?)?\.\s(?:1[89]|20)\d{2}[a-z]?\.)/u,
      );
      const end = Math.min(nextBoundary, nextEntry >= 0 ? best.yearAt + 6 + nextEntry + 1 : Infinity, best.yearAt + 420);
      found = {index: best.row.index, label: `${surname} ${year}`, text: area.text.slice(best.start, end).replace(/\s+/g, ' ').trim()};
    }
    index.cache.set(key, found);
    return found;
  }

  // "Dai et al. [2018]": the author is written before the bracket.
  function authorBefore(text, start) {
    const tail = text.slice(Math.max(0, start - 70), start);
    const match = tail.match(new RegExp(`(${NAME})(?:\\s+(?:et\\s+al\\.?|and\\s+${NAME}|&\\s+${NAME}))?['’]?s?\\s*$`, 'u'));
    return match ? match[1] : '';
  }

  function resolvePart(index, part, text, start) {
    const entries = [];
    const value = part.trim();
    if (!value) return entries;
    const labelled = index.labels.get(normalLabel(value));
    if (labelled) return [labelled];
    const years = [...value.matchAll(YEAR)].map((match) => match[0]);
    const words = value.replace(YEAR, ' ').trim();
    if (years.length && /\p{L}{2,}/u.test(words)) {
      // "Dai et al. 2018", "Umetani and Schmidt 2013", "Smith et al., 2020a, 2021",
      // "Fang et al. 2020, Zhang et al. 2021": one chunk per author.
      const chunks = [];
      for (const piece of value.split(',')) {
        const hasName = /\p{L}{2,}/u.test(piece.replace(YEAR, ''));
        if (hasName || !chunks.length) chunks.push(piece);
        else chunks[chunks.length - 1] += ` ${piece}`;
      }
      for (const chunk of chunks) {
        const chunkYears = [...chunk.matchAll(YEAR)].map((match) => match[0]);
        const surname = (chunk.match(new RegExp(`(?:(?:[Dd]e|[Vv]an|[Vv]on|[Dd]e la|[Dd]el|[Ll]e)\\s+)?${NAME}`, 'u')) || [''])[0];
        if (!surname || !chunkYears.length) return [];
        for (const year of chunkYears) {
          const entry = authorEntry(index, surname, year);
          if (!entry) return [];
          entries.push(entry);
        }
      }
      return entries;
    }
    if (years.length && !words.replace(/[,\s]/g, '')) {
      const surname = authorBefore(text, start);
      if (!surname) return entries;
      years.forEach((year) => {
        const entry = authorEntry(index, surname, year);
        if (entry) entries.push(entry);
      });
      return entries;
    }
    // "2, 34, 49" / "9–11"
    for (const piece of value.split(',')) {
      const range = piece.trim().match(/^(\d{1,3})\s*[–—-]\s*(\d{1,3})$/);
      const numbers = range
        ? Array.from({length: Math.min(30, Math.max(0, Number(range[2]) - Number(range[1]) + 1))}, (_unused, offset) => Number(range[1]) + offset)
        : /^\d{1,3}$/.test(piece.trim()) ? [Number(piece.trim())] : [];
      if (!numbers.length) {
        const label = index.labels.get(normalLabel(piece));
        if (label) entries.push(label);
        else return [];
        continue;
      }
      for (const number of numbers) {
        const entry = index.labels.get(String(number));
        if (!entry) return [];
        entries.push(entry);
      }
    }
    return entries;
  }

  function findCitations(text, index) {
    const value = String(text ?? '');
    if (!index || !index.area || !index.area.text) return [];
    const found = [];
    const patterns = [/\[([^\[\]\n]{1,160})\]/g, /\(([^()\n]{0,160}\b(?:1[89]|20)\d{2}[a-z]?)\)/g];
    for (const pattern of patterns) {
      for (const match of value.matchAll(pattern)) {
        const content = match[1];
        // Parts separated by ";" stand alone: show the ones found (an
        // entry can be garbled in the extracted list).
        const entries = content.split(';').flatMap((part) => resolvePart(index, part, value, match.index));
        if (!entries.length) continue;
        const unique = entries.filter((entry, position) => entries.findIndex((other) => other.index === entry.index && other.text === entry.text) === position);
        found.push({start: match.index, end: match.index + match[0].length, entries: unique});
      }
    }
    return found.sort((left, right) => left.start - right.start).filter((item, position, list) => position === 0 || item.start >= list[position - 1].end);
  }

  const api = {buildCitationIndex, findCitations};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PaperCitations = api;
})(typeof window !== 'undefined' ? window : globalThis);
