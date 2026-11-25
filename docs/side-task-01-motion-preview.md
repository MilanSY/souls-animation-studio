# Side Task 01 — Motion Preview

**Branche :** `feature/motion-preview`. Fonctionnalité indépendante de Kimodo et de l’inférence. Le manifeste et le ZIP public **0.5.0** sont inchangés.

- [ZIP de développement](../dist/souls_animation_studio-0.5.0-dev-motion-preview.zip)
- [Démo Blender](../demo/sas-motion-preview.blend), dérivée de [`sas-combat.blend`](../demo/sas-combat.blend)
- [Guide utilisateur](guide-utilisateur.md#motion-preview-build-de-développement)

## Audit et choix d’implémentation

Les **Motion Paths** natifs de Blender conviennent à un trajet d’objet ou d’os avec repères temporels. On peut toujours les utiliser séparément pour un chemin natif. Leurs points calculés persistent toutefois dans le `.blend` (vérifié après réouverture), et ils ne fournissent pas les ghosts d’une armature. L’onion skinning de Grease Pencil ne traite pas cette armature. Motion Preview réutilise l’évaluation de Blender et le dessin GPU de la vue 3D, sans créer de Motion Paths, d’objets, de maillages ni d’Actions temporaires.

`sampling.py` lit le depsgraph évalué à chaque temps distinct demandé, dans la couche de vue active. Les ghosts représentent les segments des os déformants si disponibles ; les trails suivent les têtes d’os ou les origines d’objets, en coordonnées monde. Les contraintes, l’IK et les contacts sont donc pris en compte. L’image et sa sous-image sont restaurées, y compris en cas d’erreur ; ni l’Action ni son slot ne sont réassignés.

`runtime.py` gère un cache en mémoire et programme une mise à jour ponctuelle, hors du rafraîchissement de la vue. Au scrub, les trails sont réutilisés et seuls les ghosts manquants sont échantillonnés. Les modifications de réglages, de rig ou sélection et les notifications du depsgraph pertinentes pour la scène invalident ou remplacent les entrées. Les callbacks de dessin utilisent des lots déjà préparés par `draw.py`. Le cache garde au plus quatre contextes scène/couche, 24 poses récentes par contexte (dont huit affichables), 32 trajectoires et 121 échantillons par trajectoire ; le pas est augmenté si nécessaire. Clear, chargement et désactivation libèrent les ressources concernées. Aucun calcul périodique permanent.

Le ZIP contient les sources de l’extension et sa licence GPL-3.0-or-later, sans poids ni code de moteur tiers. La démo reprend les données du mannequin et de l’animation de combat synthétiques du dépôt, sans ajout d’asset externe ni de script Rigify.

## Recette en fenêtre

1. Installer le ZIP de développement dans Blender 5.2 et ouvrir `demo/sas-motion-preview.blend`. Ouvrir **N → Souls Animation Studio → Motion Preview**. La vue démarre à l’image **13** ; le panneau peut être replié.
2. Vérifier le passé bleu (5 et 9), le futur orange (17 et 21) et le trajet de `SAS Training Sword`. Faire pivoter la vue ou essayer le mode filaire si le corps masque les os. Essayer **Past = 0**, puis changer l’opacité et le pas. Dans cette Action bakée, presque toutes les images sont clés : **Keys Only** resserre les ghosts autour du curseur.
3. Déplacer le curseur **13 → 17 → 13**. Le point de séparation des couleurs suit le curseur. Basculer **Keyframes**, **Direction** et **Frame Numbers**. Essayer **Hands**, puis **Selected Bones** avec un contrôleur sélectionné en mode Pose (réafficher les os dans les overlays de la démo au besoin).
4. Lancer puis arrêter la lecture : la preview doit disparaître puis revenir. Cliquer **Clear Preview** : les deux couches disparaissent et la plage est conservée. Réactiver, sauvegarder une copie puis rouvrir : le dessin est reconstruit à partir des seuls réglages.

Pour régénérer la démo, depuis la racine :

```sh
blender --background --factory-startup --offline-mode --disable-autoexec \
  --python-exit-code 1 --python scripts/build_motion_preview_demo.py
```

## Validations et limites

- **Headless, ZIP réellement installé hors ligne :** 9 tests Motion Preview, dont IK native aux sous-images, vrai Rigify avec accessoire piloté par contact, Action/slot/courbes et temps préservés, invalidation après édition d’une clé, réutilisation du cache, bornes d’échantillonnage, désactivation/réactivation et réouverture dans un nouveau processus. Les blocs JSON et binaires d’un GLB de la scène entière sont identiques avec et sans aperçu. La démo rouverte fournit 4 ghosts de 35 segments et 25 échantillons de l’épée ; l’empreinte du combat source est inchangée.
- **Non-régression :** `bash scripts/build_extension.sh`, puis validation indépendante de `SOULS_TEST_GODOT=1 bash scripts/test_headless.sh` (Godot 4.7.2, projets temporaires, aucun moteur neuronal exécuté). Les vérifications d’archive corrompue et d’échec Python remontent bien un code non nul. Le smoke suit maintenant le nouveau panneau ; le test historique de processus accepte aussi `ProcessLookupError` quand un PID disparaît pendant la lecture de `/proc`, sans changement de l’inférence.
- **En fenêtre, vérification indépendante :** inspection de captures de la vraie démo, deux couches visibles séparément, disparition après Clear. Les callbacks 3D et texte, le scrub scripté, la réutilisation du trail et la désactivation/réactivation ont été contrôlés. Le cadrage masque une partie notable des ghosts derrière le personnage. La recette reste utile pour la manipulation manuelle de la timeline, les autres cibles et la lisibilité selon le cadrage.
- **Mesures :** Linux, Blender 5.2.1 LTS / Python embarqué 3.13.13. Sur la fixture IK synthétique, environ **11 ms CPU** pour reconstruire et **0,5 ms** avec le cache ; environ **62 ms CPU** pour inspecter et échantillonner cinq temps sur la fixture Rigify. Indications ponctuelles, pas des seuils. La session en fenêtre utilisait une Radeon RX 6600 ; aucun débit d’images ni coût GPU mesuré, et la fluidité sur **Intel HD Graphics 520 reste à valider**. Le rendu n’a pas fait l’objet d’un test séparé.
- **Limites :** silhouette de maillage reportée. Les cibles des rigs génériques sont reconnues par nom ; **Selected Bones** sert de solution de repli. Les repères viennent des Actions assignées et de leurs entrées détectées, pas des strips NLA ordinaires, des drivers ou des simulations ; leurs effets évalués, eux, peuvent être visibles. Le déplacement temporel repose sur `frame_set` : le résultat n’est pas garanti pour une simulation dépendante de l’ordre des images ni pour des scripts de scène réagissant à chaque changement de temps. Les numéros sont espacés pour limiter l’encombrement.
