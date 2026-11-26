// Chiffre les observations de chaque équipe avec le code entraîneur (secret COACH_CODE)
// et les range dans docs/data/<slug>.json. Les observations en clair ne sont jamais publiées.
import { webcrypto as c } from 'node:crypto';
import fs from 'node:fs';

const code = process.env.COACH_CODE;
if (!code) { console.log('COACH_CODE absent : observations non chiffrées, rien à faire.'); process.exit(0); }

const b64 = x => Buffer.from(x).toString('base64');
const equipes = JSON.parse(fs.readFileSync('equipes.json', 'utf8'));

for (const { slug } of equipes) {
  const obsFile = `travail/${slug}-obs.json`, dataFile = `docs/data/${slug}.json`;
  if (!fs.existsSync(obsFile) || !fs.existsSync(dataFile)) { console.log(`${slug} : pas de nouvelles observations`); continue; }
  const obs = JSON.parse(fs.readFileSync(obsFile, 'utf8'));
  if (!Array.isArray(obs.items) || !obs.items.length) { console.log(`${slug} : observations vides, ignorées`); continue; }
  const data = JSON.parse(fs.readFileSync(dataFile, 'utf8'));
  const iter = 310000;
  const salt = c.getRandomValues(new Uint8Array(16)), iv = c.getRandomValues(new Uint8Array(12));
  const km = await c.subtle.importKey('raw', new TextEncoder().encode(code), 'PBKDF2', false, ['deriveKey']);
  const key = await c.subtle.deriveKey({ name: 'PBKDF2', salt, iterations: iter, hash: 'SHA-256' }, km, { name: 'AES-GCM', length: 256 }, false, ['encrypt']);
  const ct = new Uint8Array(await c.subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(JSON.stringify(obs))));
  data.coach = { salt: b64(salt), iv: b64(iv), ct: b64(ct), iter, j: data.lastPlayed, at: new Date().toISOString() };
  fs.writeFileSync(dataFile, JSON.stringify(data, null, 1));
  console.log(`${slug} : ${obs.items.length} observations chiffrées (J${data.lastPlayed})`);
}
