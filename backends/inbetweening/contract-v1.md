# Proposition de contrat externe `sas.inbetween` v1 — aucun moteur activé

L'adaptateur d'animation reste propriétaire de l'action, de son slot, du rig et du bake. Un moteur local dans son propre environnement ne reçoit que `request.json` et écrit `response.json` dans un dossier temporaire. Pas de réseau, d'objets Blender, d'assets d'accessoires, de chemins de projet ni de sérialisation exécutable. JSON UTF-8, champs exacts, version entière obligatoire, tableaux à dimensions bornées, doublons et nombres non finis refusés. Chaque job a un `request_id` opaque unique copié dans la réponse ; pas de fallback implicite à la référence CPU.

## Requête et réponse proposées

| Champ de requête | Sémantique |
|---|---|
| `schema`, `version`, `request_id` | `sas.inbetween.request`, `1`, jeton du job |
| `fps` | `[numérateur, dénominateur]` entiers strictement positifs |
| `joints` | En ordre topologique : `{id,parent,rest_translation,rest_rotation}` ; `parent` est un indice antérieur ou `null` pour l'unique racine. `id` est un identifiant stable, non un nom de contrôleur. |
| `anchors` | Liste chronologique d'au moins deux `{frame,translations,rotations}`. `frame` inclut les sous-images ; un tableau par type, dans l'ordre `joints`. Chaque pose entière est verrouillée, y compris les ancres provenant des clés originales non sélectionnées dans la plage. |
| `sample_frames` | Liste triée et unique de toutes les images entières libres strictement entre les ancres extrêmes. N'inclut aucune ancre. Pas d'extrapolation. |
| `contacts` | Liste de `{joint,kind,samples}`. `kind` vaut `position` ou `pose` ; chaque échantillon actif est `{frame,position,rotation,position_tolerance,angle_tolerance}`. Pour `position`, `rotation` et `angle_tolerance` sont `null`. Inclure toutes les ancres et images demandées sur lesquelles le contact doit être contrôlé. Pour un accessoire mobile, l'adaptateur échantillonne son point de prise, sans transmettre l'objet ni ses chemins. |

Conventions : mètres, radians, main droite Z-up, quaternions unitaires WXYZ ; rest et poses sont des transformations **locales absolues par rapport au parent**, racine dans le repère d'échange. Contacts et cibles exprimés dans ce même repère d'échange, évalués après composition. Cette représentation ne prétend pas couvrir les échelles animées ni les canaux discrets des contrôleurs : les conserver côté Blender ou refuser une demande non représentable. Capturer avant lancement le mapping explicite canonique ↔ rig et les références à l'action, au slot et aux cibles. Un modèle avec un autre squelette, repère ou pas de temps nécessite une conversion explicite et réversible sur les canaux édités ; ne jamais déduire la correspondance à partir du seul nombre d'articulations.

Réponse : `schema="sas.inbetween.response"`, `version=1`, même `request_id`, `samples` (exactement une pose `{frame,translations,rotations}` pour chaque entrée de `sample_frames`, dans le même ordre). Aucune édition d'ancre ni d'accessoire dans la réponse. Un code de sortie non nul, une réponse absente/dupliquée/partielle, une dimension inattendue, une rotation non unitaire ou une scène devenue périmée échouent sans application. Pas d'action générée ni remplacée en cas d'annulation ou de délai dépassé.

## Validation après retargeting et bake

1. Créer une **copie non activée** de l'action ; écrire les propositions uniquement aux images autorisées. Garder le slot, les clés originales et le frame courant de la source intacts. Restaurer action/slot/temps même après erreur si la copie est temporairement évaluée.
2. Comparer les valeurs exactes des clés et les matrices de poses évaluées aux ancres, sur le même rig et le même graphe de contraintes. Les sous-images ancrées comptent. La fidélité numérique du moteur ou son conditionnement ne remplacent pas cette vérification.
3. Mesurer distance et erreur angulaire, quand demandée, **sur le rig évalué** (pied-sol ou main-accessoire) aux échantillons spécifiés, après retargeting et résolution locale éventuelle. Chaque dépassement, cible introuvable ou conflit avec une ancre bloque la validation et entraîne le rejet de la copie, avec diagnostic frame/articulation/résidus. Ne pas déplacer l'accessoire ni écraser l'ancre pour « réussir ». Pas de garantie aux sous-images non échantillonnées ni après un export non vérifié.

Première qualification : fixtures sans poids, avec ancre fractionnaire, accessoire mobile, et conflit ancre/contact qui doit échouer ; puis mesurer sur le matériel cible la durée, la mémoire, les erreurs post-bake et le résultat exporté. CondMDI et MDM restent des candidats de recherche, pas des options UI. Le [moteur CPU actuel](README.md) reste la référence déterministe distincte ; son mode Contacts explicite peut retourner un rapport de violations, ce qui n'est pas une validation de ce futur contrat.
