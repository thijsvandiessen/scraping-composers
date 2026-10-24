// Starter queries for the gold graph, loaded by `composer-ingest export-neo4j`.
// Paste them into Neo4j Browser (http://localhost:7475). Each one stands alone.
//
// The graph holds facts, not scores:
//
//   (:Person|Ensemble)-[:PERFORMED_IN {role, discipline}]->(:Concert|Recording)
//   (:Concert|Recording)-[:PROGRAMMED {times}]->(:Work)-[:COMPOSED_BY]->(:Person)
//   (:Person)-[:BORN_IN|DIED_IN|CITIZEN_OF]->(:Place)
//   (:Person)-[:HAS_GENRE|IN_MOVEMENT|HAS_PROFESSION|ASSOCIATED_PERIOD]->(...)
//   (:Genre)-[:SUBCLASS_OF]->(:Genre), (:Movement)-[:SUBCLASS_OF]->(:Movement)
//   (:Person)-[:STUDENT_OF|TEACHER_OF|INFLUENCED_BY]->(:Person)
//   (:Person)-[:CHILD_OF|PARENT_OF|SPOUSE_OF|SIBLING_OF]->(:Person)
//
// so a connection's strength is whatever you count along the paths. Raw counts
// mostly measure popularity (everyone is "strongly connected" to Beethoven), so
// the normalised versions below are usually the more interesting ones.


// --- performer → composer -------------------------------------------------

// Who championed whom: events where a performer played a composer's music.
MATCH (p:Person|Ensemble)-[:PERFORMED_IN]->(e)-[:PROGRAMMED]->(:Work)-[:COMPOSED_BY]->(c:Person)
WHERE p <> c
RETURN p.label AS performer, c.label AS composer, count(DISTINCT e) AS events
ORDER BY events DESC LIMIT 25;

// The same, for one performer, as a share of everything they played.
MATCH (p:Person {label: 'Beinum, Eduard van'})-[:PERFORMED_IN]->(e)-[:PROGRAMMED]->(:Work)
      -[:COMPOSED_BY]->(c:Person)
WITH c, count(DISTINCT e) AS events
WITH collect({composer: c.label, events: events}) AS rows, sum(events) AS total
UNWIND rows AS row
RETURN row.composer AS composer, row.events AS events, round(100.0 * row.events / total, 1) AS pct
ORDER BY events DESC LIMIT 25;


// --- composer ↔ composer --------------------------------------------------

// Programmed on the same concert night, raw.
MATCH (a:Person)<-[:COMPOSED_BY]-(:Work)<-[:PROGRAMMED]-(e:Concert)-[:PROGRAMMED]->(:Work)
      -[:COMPOSED_BY]->(b:Person)
WHERE elementId(a) < elementId(b)
RETURN a.label AS a, b.label AS b, count(DISTINCT e) AS concerts
ORDER BY concerts DESC LIMIT 25;

// Normalised by how often each is programmed at all (cosine: shared / √(a·b)).
// Pairs that are almost always programmed together rise to the top: the
// Strauss family, operetta (Lehár, Kálmán), and so on.
MATCH (c:Person)<-[:COMPOSED_BY]-(:Work)<-[:PROGRAMMED]-(e:Concert)
WITH c, count(DISTINCT e) AS total WHERE total >= 20
WITH c AS a, total AS ta
MATCH (a)<-[:COMPOSED_BY]-(:Work)<-[:PROGRAMMED]-(e:Concert)-[:PROGRAMMED]->(:Work)-[:COMPOSED_BY]->(b:Person)
WHERE elementId(a) < elementId(b)
WITH a, ta, b, count(DISTINCT e) AS shared WHERE shared >= 10
MATCH (b)<-[:COMPOSED_BY]-(:Work)<-[:PROGRAMMED]-(eb:Concert)
WITH a, ta, b, shared, count(DISTINCT eb) AS tb
RETURN a.label AS a, b.label AS b, shared, round(shared / sqrt(toFloat(ta) * tb), 3) AS cosine
ORDER BY cosine DESC LIMIT 25;


// --- performer ↔ performer ------------------------------------------------

// Who shared the stage (or the recording session) most.
MATCH (a:Person|Ensemble)-[:PERFORMED_IN]->(e)<-[:PERFORMED_IN]-(b:Person|Ensemble)
WHERE elementId(a) < elementId(b)
RETURN a.label AS a, b.label AS b, count(DISTINCT e) AS events
ORDER BY events DESC LIMIT 25;


// --- teachers, influence, family (Wikidata) ------------------------------

// Lineages: the teaching network around one person, three steps out. Wikidata
// records a pairing from either side (A student_of B, or B teacher_of A), so
// the pattern is undirected; the arrows in Browser show which way each runs.
MATCH path = (:Person {label: 'Johannes Brahms'})-[:STUDENT_OF|TEACHER_OF*1..3]-(:Person)
RETURN path LIMIT 50;

// The most influential teachers, by students recorded either way round.
MATCH (t:Person)
OPTIONAL MATCH (t)<-[:STUDENT_OF]-(a:Person)
OPTIONAL MATCH (t)-[:TEACHER_OF]->(b:Person)
WITH t, collect(DISTINCT a) + collect(DISTINCT b) AS students
UNWIND students AS student
WITH t, count(DISTINCT student) AS students WHERE students > 0
RETURN t.label AS teacher, students ORDER BY students DESC LIMIT 25;

// Do teacher and student turn up on the same programmes? A recorded
// relationship backed by performance data.
MATCH (s:Person)-[:STUDENT_OF]->(t:Person)
MATCH (s)<-[:COMPOSED_BY]-(:Work)<-[:PROGRAMMED]-(e:Concert)-[:PROGRAMMED]->(:Work)-[:COMPOSED_BY]->(t)
RETURN s.label AS student, t.label AS teacher, count(DISTINCT e) AS concerts
ORDER BY concerts DESC LIMIT 25;


// --- shared biography -----------------------------------------------------

// Composers born in the same place, by place.
MATCH (c:Person)<-[:COMPOSED_BY]-(:Work)
WITH DISTINCT c
MATCH (c)-[:BORN_IN]->(place:Place)
RETURN place.label AS place, count(c) AS composers, collect(c.label)[0..8] AS sample
ORDER BY composers DESC LIMIT 25;

// Everything two people have in common, as paths (view as a graph in Browser).
MATCH (a:Person {label: 'Johannes Brahms'}), (b:Person {label: 'Robert Schumann'})
MATCH path = (a)-[*1..2]-(x)-[*0..1]-(b)
WHERE NOT x:Work AND NOT x:Concert AND NOT x:Recording
RETURN path LIMIT 50;


// --- genres ---------------------------------------------------------------

// Roll-up: everyone tagged with dance music or any genre below it (waltz,
// Viennese waltz, ...). Match the top on wikidata_id, not label: labels follow
// Wikidata's renames, the QID does not. *0.. includes dance music itself.
MATCH (p:Person)-[:HAS_GENRE]->(:Genre)-[:SUBCLASS_OF*0..]->(:Genre {wikidata_id: 'Q316930'})
RETURN DISTINCT p.label AS person
ORDER BY person LIMIT 50;

// Genres with no path up to a broader one: the ones the hierarchy cannot roll up.
MATCH (g:Genre)<-[:HAS_GENRE]-(p:Person)
WHERE NOT (g)-[:SUBCLASS_OF]->()
RETURN g.label AS genre, g.wikidata_id AS qid, count(p) AS persons
ORDER BY persons DESC LIMIT 25;


// --- Graph Data Science ---------------------------------------------------

// Project a weighted performer → composer graph into GDS (in memory; drop it
// with CALL gds.graph.drop('performers') when done).
MATCH (p:Person|Ensemble)-[:PERFORMED_IN]->(e)-[:PROGRAMMED]->(:Work)-[:COMPOSED_BY]->(c:Person)
WITH p, c, count(DISTINCT e) AS weight WHERE weight >= 3
WITH gds.graph.project('performers', p, c, {relationshipProperties: {weight: weight}}) AS g
RETURN g.nodeCount AS nodes, g.relationshipCount AS rels;

// Performers with the most similar repertoire (weighted Jaccard over the
// composers they play). A similarity of 1.0 between a person and an ensemble
// named after them is often one entity recorded twice: a dedupe lead.
CALL gds.nodeSimilarity.stream('performers', {relationshipWeightProperty: 'weight', topK: 3, degreeCutoff: 10})
YIELD node1, node2, similarity
WITH gds.util.asNode(node1) AS a, gds.util.asNode(node2) AS b, similarity
WHERE elementId(a) < elementId(b)
RETURN a.label AS a, b.label AS b, round(similarity, 3) AS similarity
ORDER BY similarity DESC LIMIT 50;

// Communities: clusters of performers and the composers they play
// (baroque, French/German romantic, Italian opera, ...).
CALL gds.louvain.stream('performers', {relationshipWeightProperty: 'weight'})
YIELD nodeId, communityId
WITH communityId, collect(gds.util.asNode(nodeId).label) AS members
RETURN communityId, size(members) AS size, members[0..10] AS sample
ORDER BY size DESC LIMIT 15;

