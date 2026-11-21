/* The offline catalog shares its vocabulary and ranking rules with library_search.py. */
function createLibrarySearch(rules) {
  const normalize = text => String(text ?? '').normalize('NFKC').toLowerCase().trim();
  const baseVocabulary = rules.concepts.flatMap((concept, index) =>
    [...concept.terms, ...(concept.forms || [])].map(term => [normalize(term), index]));
  const phrases = rules.phrases.map(row => [new RegExp(row.pattern, 'g'), row.term]);

  function queryPlan(terms, records) {
    let query = normalize(terms.join(' '));
    if (rules.negative_qualifiers.some(word => query.includes(word))) return [];
    for (const [pattern, term] of phrases) query = query.replace(pattern, term);
    const keywords = [...new Set(records.flatMap(row => normalize(row.keywords).split(/\s+/)))]
      .filter(term => term.length >= 2 && term.length <= 32).sort();
    const vocabulary = [...baseVocabulary, ...keywords.map(term => [term, 'literal']),
      ...rules.fillers.map(term => [normalize(term), null])];
    function priority([word, source]) {
      if (source === null) return 1;
      if (source === 'literal') return 2;
      return word.length === 1 ? 3 : 0;
    }
    vocabulary.sort((a, b) => priority(a) - priority(b) || b[0].length - a[0].length);
    const selected = new Set();
    const knownLiterals = [];
    let remaining = '';
    let position = 0;
    while (position < query.length) {
      const match = vocabulary.find(([term]) => query.startsWith(term, position));
      if (match) {
        const [term, index] = match;
        if (index === 'literal') knownLiterals.push(term);
        else if (index !== null) selected.add(index);
        remaining += ' ';
        position += term.length;
      } else {
        remaining += query[position++];
      }
    }
    if (!selected.size && !knownLiterals.length) return [];
    const literals = [...new Set([...knownLiterals, ...(remaining.match(/[a-z0-9_\u3400-\u9fff]+/g) || [])])];
    const groups = [...selected].sort((a, b) => a - b).map(index => ({
      words: rules.concepts[index].terms.map(normalize),
      onCard: rules.concepts[index].evidence === 'card'
    }));
    return [...groups, ...literals.map(term => ({words: [term], onCard: false}))];
  }

  function groupScore(record, groups) {
    return groups.reduce((total, {words}) => total + Math.max(0, ...words.map(term =>
      Object.entries(rules.weights).reduce((sum, [field, weight]) =>
        sum + (record[field].includes(term) ? weight : 0), 0))), 0);
  }

  function rank(records, terms) {
    terms = terms.map(normalize).filter(Boolean);
    if (!terms.length) return {mode: 'all', ids: records.map(row => row.id)};
    records = records.map(row => ({...row, ...Object.fromEntries(
      Object.keys(rules.weights).map(field => [field, normalize(row[field])]))}));
    let candidates = records.filter(row => terms.every(term => row.text.includes(term)));
    let groups = terms.map(term => ({words: [term], onCard: false}));
    let mode = 'exact';
    if (!candidates.length) {
      groups = queryPlan(terms, records);
      candidates = records.filter(row => {
        const card = row.title + ' ' + row.keywords;
        return groups.length && groups.every(({words, onCard}) =>
          words.some(term => (onCard ? card : row.text).includes(term)));
      });
      mode = 'related';
    }
    candidates.sort((a, b) => groupScore(b, groups) - groupScore(a, groups) ||
      (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    if (mode === 'related') candidates = candidates.slice(0, rules.fallback_limit);
    return {mode, ids: candidates.map(row => row.id)};
  }
  return {rank, queryGroups: (terms, records = []) => queryPlan(terms, records).map(group => group.words)};
}
if (typeof module !== 'undefined') module.exports = createLibrarySearch;
