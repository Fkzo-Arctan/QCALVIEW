## 40.22 — Version de dépôt

### Nouveautés par rapport à 40.21

- **Multi-View** : jusqu’à quatre visionneuses de points de vue simultanées, affichage Quad View ou fenêtres indépendantes ; chaque visionneuse conserve son orientation, sa projection et son état temporaire, y compris plusieurs vues du même PDV.
- **Coordination des rendus** : traitement séquentiel partagé, caches de photographies et géométries bornés, rendu secondaire léger lors des modifications communes suivi d’un affinement, sans seconde reconstruction complète des panoramas.
- **Synchronisation et enregistrement** : activation d’une visionneuse synchronisée avec le cockpit QCALVIEW, commande « Enregistrer l’état du point de vue » propre à chaque visionneuse, sauvegarde des sessions Multi-View dans le projet QGIS.
- **Panoramas** : gestion améliorée des murs extrudés 2,5D et des géométries traversant la couture à ±180°.
- **Rasters altimétriques** : sélection des rasters numériques monobandes géoréférencés, sans filtrage par nom ni exclusion des terrains plats ; prise en charge de pas d’échantillonnage jusqu’à 0,5 m. Pas proposé par défaut : 25 m (anciennes valeurs enregistrées respectées).
- **Altitude absolue** : option « Alt. absolue » à côté de « Alt./Z », persistée par point de vue dans `qcv_altabs`; utilisation directe de la cote de caméra au lieu d’ajouter la hauteur au MNT ; option de SCR vertical du projet ou SCR vertical spécifique dans les paramètres. Comparaison indicative/certifiée avec le MNT en fonction des références, sans conversion implicite.
- **Métadonnées** : distinction altitude absolue/hauteur relative dans les exports QCALVIEW ; une cote verticale explicite n’est pas injectée arbitrairement dans le champ EXIF GPSAltitude.
- **Traductions** : suppression du traducteur Qt global de QCALVIEW qui pouvait écraser les traductions françaises des menus natifs QGIS (notamment au clic droit sur une couche). Traductions françaises appliquées uniquement aux composants du plugin ; catalogue TS complété.

### Hors périmètre

Cette branche de publication reste basée sur **40.21**

### Validation

Vérification native sur QGIS 3.44 et QGIS 4.x sous Windows recommandée avant diffusion.

## 40.22

- DEM/DSM selector now includes any valid georeferenced, one-band numeric raster regardless of filename, QGIS DEM heuristics or elevation range (constant terrain supported).
- "Show all rasters" remains available to view other raster layers; verify a raster contains elevation measurements before using it as terrain.
- Terrain sampling now accepts 0.5 m as its minimum; arrow-step presets: 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 150, 200, 500, 1000, 2000 m. Arbitrary values from 0.5 to 2000 m may also be entered.
- The radial sampling minimum used by relief masks is also reduced to 0.5 m (from 1 m). Fine LiDAR DSM spacing can be costly. Existing adaptive rendering/safe-mode limits remain in force, so the requested spacing may be coarsened when safeguarding expensive renders.
- Multi-View with up to four simultaneous viewpoint viewers
- Shared, sequential render coordinator to avoid concurrent renderer instances
- Bounded shared photo cache and retained camera-keyed geometry/overlay caches across viewer context switches
- Independent temporary camera state per viewer, including multiple views of the same PDV
- Active-view synchronization with the QCALVIEW cockpit
- Integrated Quad View plus independent-window mode
- Automatic propagation of projected-layer and viewpoint geometry changes to open viewers
- Camera-only edits (azimuth, pitch, roll, FOV, offsets, etc.) refresh only the active viewer
- Lightweight secondary renders for shared-scene edits followed by automatic high-quality refinement
- Compact Quad View interface: information side panels hidden and per-view controls reduced to the essentials
- Per-viewer **Save viewpoint state** command (same parameters + visual state persistence as 40.21)
- Multi-View session persistence in the QGIS project
- Seam-safe panoramic 2.5D wall projection for lines and polygon extrusions; filled walls now follow the projected path across the ±180° panorama seam
- Background Multi-View refinement no longer triggers a second full-resolution panorama render, reducing peak memory use with raster draping

## 40.21

- Display raster on DEM
- Magnifier for the monoplotting tool
- Apply QCALVIEW styles to QGIS
- Revised theme handling
- From experimental to stable
