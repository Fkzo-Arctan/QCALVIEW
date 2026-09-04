## ALPHA-40.20

- Experimental Alpha candidate for public QGIS testing.
- Public experimental-tool access reduced to Projection grid, Azimuth ruler and Interactive monoplotting.

# QCALVIEW — Changelog


---

Historical development notes consolidated for release preparation.


---

# QCALVIEW 40.19.8 — précontrôle d’état avant export batch

- Détection de l’état visuel courant non enregistré avant un export batch : thème QGIS, couches projetées et réglages visuels.
- Si le PDV courant est concerné par le batch et que son état diffère de l’état persisté, QCALVIEW avertit avant tout changement de PDV.
- Choix par défaut : enregistrer « paramètres + état » puis poursuivre l’export.
- Si l’utilisateur refuse l’enregistrement, le batch est annulé afin d’éviter un export silencieusement différent de l’aperçu.
- Vérification après enregistrement : si l’état ne peut pas être confirmé comme persisté, l’export est annulé avec un message explicite.
- Le contrôle est commun aux exports batch sélectionnés, composites et overlays.

Aucune modification des moteurs de rendu, du z-buffer, du chargement image ou de l’interface.


---

# QCALVIEW 40.19.7 — améliorations fonctionnelles ciblées

- Fermeture automatique de la Visionneuse lorsque le dock QCALVIEW est fermé.
- Azimut cyclique modulo 360° :
  - flèches/molette : passage direct 0° ↔ 359.xx selon le pas actif ;
  - saisie clavier libre (valeurs négatives ou supérieures à 360°) puis normalisation à la validation ;
  - exemples : -1° → 359°, 520° → 160°, 720° → 0°.
- Style topographique unifié : une seule couleur et une seule épaisseur pour Opaque, Wireframe, Skyline et Ridgelines. Les options de pointillés restent propres aux modes concernés.
- Onglet Export :
  - boutons « Tout cocher » et « Tout décocher » agissant sur toutes les lignes ;
  - coloration discrète des lignes cochées ;
  - hauteur du tableau adaptée au nombre de PDV et à la hauteur disponible de la fenêtre ;
  - ascenseur vertical conservé lorsque toutes les lignes ne tiennent pas à l'écran.

Aucune modification des moteurs de rendu PINHOLE/PANORAMA, du z-buffer, des limites de sécurité ou du chargement des grandes images.


---

# QCALVIEW 40.19.6 — chargement robuste des très grands panoramas

## Objectif

Permettre le chargement de panoramas JPEG très haute résolution sous QGIS 3.44/Qt5 et QGIS 4/Qt6 sans tenter de décoder immédiatement l'image complète en QImage.

Cas de validation cible : panorama cylindrique 57 000 × 5 700 px (~325 MP, ~1.21 GiB une fois décodé en 32 bits) pour ~74 Mo sur disque.

## Changements

- Nouveau `core/_image_io.py` commun QGIS 3.44 / QGIS 4 :
  - lecture des dimensions par `QImageReader.size()` sans décodage plein format ;
  - proxy interactif borné pour les très grandes images ;
  - décodage JPEG directement à la taille utile via `QImageReader.setScaledSize()` ;
  - journalisation claire des dimensions source, mémoire QImage estimée et taille du proxy ;
  - récupération de `QImageReader.errorString()` en cas d'échec ;
  - gestion temporaire et restaurée de `QImageReader.setAllocationLimit()` uniquement pour une lecture pleine résolution explicite, avec contrôle mémoire préalable.
- `load_photo()` et `_camera_load_photo_from_path()` utilisent le même chemin robuste ; la visionneuse reçoit le proxy QImage et ne tente plus de charger implicitement le JPEG géant via QPixmap.
- `_camera_read_photo_metadata()` lit les dimensions via l'en-tête, sans `QImage(path)`.
- Les lectures XMP DJI/GPano sont limitées à la zone de métadonnées en tête de fichier, au lieu de lire le JPEG complet en mémoire.
- Lecture EXIF Pillow compatible avec les panoramas > seuil `DecompressionBomb` : le seuil est neutralisé uniquement le temps d'une lecture d'en-tête/EXIF, sans décodage des pixels.
- Projection : hors GPano/XMP explicite, QCALVIEW ne force plus `PINHOLE` pour une image dont le ratio n'est pas 2:1. La projection choisie par l'utilisateur est conservée. Le ratio 2:1 reste une heuristique EQUIRECT prudente.
- En export plein format, les pixels source restent la référence : QCALVIEW ne remplace jamais silencieusement la photo par le proxy. Une erreur explicite est levée si la mémoire disponible est insuffisante.

## Non-régression

- Aucun changement du moteur PINHOLE.
- Aucun changement du moteur PANORAMA, du z-buffer, du culling terrain ou des styles QML.
- Aucun changement des fichiers d'interface.


---

# QCALVIEW 40.19.5 — compatibilité QgsMessageBar QGIS 4

- Corrige les appels `QgsMessageBar.pushMessage()` utilisant des niveaux numériques (`0`/`1`), refusés par QGIS 4.
- Utilise les enums compatibles centralisés dans `core/_compat.py` (`Qgis.MessageLevel`).
- Corrige les messages des guides image / centre image, GCP et outils de monoplotting.
- Aucun changement du moteur de rendu, du QML PDV ou de l’interface.


---

# QCALVIEW 40.19.4 — diagnostic du style PDV QML

- Le renderer PDV reste entièrement défini par `core/style/STYLE-PDV.qml`.
- Ajout d'une journalisation détaillée autour de `_after_camera_layer_changed()`.
- Journalisation du chemin QML, existence/lecture du fichier, valeur brute retournée par `loadNamedStyle()`, type de renderer obtenu et valeurs de variables FOV.
- Les exceptions auparavant silencieuses sont désormais écrites dans **Messages > QCALVIEW** avec traceback.
- Avertissement dans la barre de messages QGIS en cas d'échec global de mise à jour PDV.


---

# QCALVIEW 40.19.3 — temporisation et freeze des réglages de rendu

## Objectif

Éviter les recalculs inutiles lorsque l’utilisateur fait varier rapidement les paramètres de calage ou saisit une valeur au clavier. Le moteur ne doit calculer que l’état final réellement demandé.

## Debounce global

- Les variations à la molette, aux flèches ou par clic répété redémarrent un timer unique.
- Paramètres lourds (azimut, tangage, roulis, altitude caméra, HFOV/VFOV, distance de vue, pas DEM…) : ~380 ms hors faible latence, ~180 ms en faible latence.
- Les autres contrôles numériques utilisent un délai plus court ; toggles/combos conservent une réponse rapide.
- Plusieurs paramètres modifiés dans la même rafale sont coalescés en un seul rendu.

## Freeze de saisie manuelle

- Dès qu’un nombre est saisi directement dans un spinbox, le rendu est gelé.
- Aucun état intermédiaire (1 → 11 → 115, etc.) n’est rendu ni enregistré dans le brouillon caméra.
- Entrée ou perte de focus valide la saisie puis déclenche un seul rendu final.

## Render Request generation

- Chaque changement incrémente une génération logique.
- Le timer n’exécute que la génération la plus récente.
- Une demande devenue obsolète avant son lancement est ignorée.

## Portée

- Le mécanisme est placé au niveau du contrôleur de rendu et vaut pour PINHOLE, EQUIRECT et CYLINDRICAL sans modifier leurs moteurs de projection.
- Aucun fichier `.ui` n’est modifié.
- Le culling terrain et le z-buffer PANORAMA de 40.19.2 sont inchangés.


---

# QCALVIEW 40.19.2 — culling terrain avant faces et contours terrain-aware

## Objectif

Réduire le coût du moteur PANORAMA avant triangulation/rasterisation et supprimer les deux crashs observés en 40.19.1 dans les tests de visibilité terrain (`np.searchsorted` / répétitions `atan2`).

## Culling terrain en amont

- EQUIRECT et CYLINDRICAL utilisent le même chemin PANORAMA.
- Les polygones et volumes sont testés contre l'horizon MNT **avant** triangulation, projection des faces et insertion au z-buffer.
- Les lignes extrudées sont testées avant projection/maillage des murs.
- Les objets schématiques disposent d'un pré-test basé sur leur hauteur représentée avant génération de centaines de billboards lorsque cette hauteur peut être déterminée.
- Les objets très longs sont volontairement classés `mixed` plutôt que supprimés agressivement afin de préserver une portion visible éventuelle.

## Horizon rapide PANORAMA

- Ajout d'une petite LUT distance → index d'horizon construite une fois avec le cache MNT.
- Les tests panoramiques scalaires n'appellent plus `np.searchsorted` par sommet/face.
- Le comportement partagé/PINHOLE de `_is_visible_by_horizon()` reste inchangé.
- Pour un objet `mixed`, un culling de face conservateur peut encore être appliqué, mais uniquement sur cette faible fraction des objets et avec trois sommets seulement.

## Contours et relief

- Les contours physiques différés conservent désormais leurs coordonnées XYZ monde.
- Après le z-buffer objet/objet, chaque segment densifié est aussi testé contre le relief (extrémités + milieu).
- Un objet totalement masqué par le MNT est rejeté avant la création de ses faces **et de ses contours**.
- Les portions de contours masquées par le terrain sont rejetées même lorsque la face correspondante n'est plus présente dans le depth-buffer.

## Diagnostic

L'état de rendu peut indiquer :

- nombre d'objets rejetés avant calcul des faces ;
- objets AVR non générés car masqués par le relief ;
- segments de contours masqués par le relief.

## Non-régression

- Aucun changement du moteur de rendu rapide PINHOLE.
- Aucun changement d'interface `.ui`.
- Le z-buffer radial PANORAMA de 40.19.1 et son rasterizer borné restent la base du rendu.


---

# QCALVIEW 40.19.1 — stabilisation du rasterizer PANORAMA et culling relief

## Objectif

Conserver le moteur PANORAMA commun EQUIRECT/CYLINDRICAL validé en 40.19, mais réduire fortement le coût et le risque natif du z-buffer lorsque la scène contient beaucoup de faces. Le relief est désormais exploité comme premier niveau de rejet avant la rasterisation fine.

## Corrections

- **PANO-ZBUF-STAB** : nouveau rasterizer spécifique aux panoramas ; le rasterizer PINHOLE historique reste inchangé.
  - suppression de `np.meshgrid()` dans le hot-path panoramique ;
  - coordonnées X/Y sous forme de vues broadcastées, sans deux grandes copies par tuile ;
  - barycentriques et profondeurs en `float32` ;
  - buffers de travail réutilisés d'une tuile à l'autre ;
  - taille de tuile bornée à 160 px pour limiter le pic de mémoire temporaire.
- **SIMPLE-FILL FAST PATH** : les aplats simples (BATI, murs, volumes classiques) n'allouent plus un tableau RGBA complet par tuile. La couleur constante est écrite directement dans le framebuffer aux pixels gagnants.
- **FRONT-TO-BACK / EARLY-Z** : les faces panoramiques sont traitées du proche vers le lointain. Une tuile déjà entièrement occupée par une profondeur plus proche que la face courante est rejetée avant les calculs barycentriques/couleur.
- **TERRAIN-CULLING** : lorsque l'occultation par le relief est active, les faces entièrement cachées par le cache d'horizon distance-aware sont supprimées avant conversion/rasterisation.
  - test conservateur sur les 3 sommets, le centroïde et les 3 milieux d'arêtes ;
  - epsilon légèrement élargi pour éviter de supprimer un fragment réellement visible ;
  - applicable aux surfaces BATI, murs, volumes de lignes et primitives schématiques ;
  - pipeline identique pour EQUIRECT et CYLINDRICAL.
- **RUNTIME MEMORY STOP** : le rasterizer panoramique complète le Memory Guard par une vérification périodique de la mémoire réellement disponible. Si le processus atteint une marge native dangereuse, la frame est arrêtée proprement avant l'access violation, avec message dans l'état du rendu.
- **DIAGNOSTIC** : QCALVIEW conserve désormais des compteurs de faces rejetées par le relief, faces rasterisées et tuiles rejetées par early-Z.

## Pipeline PANORAMA

`culling distance/FOV -> culling objet -> culling relief -> faces visibles -> tri proche/loin -> early-Z par tuile -> z-buffer fin -> contours depth-tested -> annotations`

Le relief sert donc de masque hiérarchique économique avant le framebuffer objet/objet, au lieu d'envoyer aveuglément toutes les faces au rasterizer.

## Non-régression

- Aucun fichier UI modifié.
- `projector.py` inchangé.
- Le moteur PINHOLE continue d'utiliser `_compose_faces_zbuffer()` historique ; les optimisations 40.19.1 sont cantonnées à `_compose_panorama_faces_zbuffer_40191()`.


---

# QCALVIEW 40.19 — composition PANORAMA et contours depth-tested

## Objectif

Stabiliser la composition finale du moteur PANORAMA commun EQUIRECT/CYLINDRICAL après validation du z-buffer radial 40.18.9.

## Corrections

- **PANO-PASSES** : ordre de rendu explicite `surfaces z-buffer -> topographie derrière -> contours physiques -> annotations`.
  - Le wireframe, la skyline, les ridgelines et le relief opaque ne peuvent plus passer graphiquement devant les bâtiments/polygones.
  - Le calcul d’occultation par le MNT/horizon reste inchangé.
  - Aucun buffer topographique pleine résolution supplémentaire : la topo est peinte en `DestinationOver` dans l’overlay existant.
- **PANO-EDGE-Z** : les contours des surfaces/volumes panoramiques sont différés puis testés contre le même z-buffer radial que les faces.
  - Les arêtes d’un bâtiment lointain ne doivent plus traverser un volume proche.
  - Gestion commune de la couture 0/360 pour EQUIRECT et CYLINDRICAL.
  - Échantillonnage local 3x3 du depth-buffer pour éviter les réapparitions dues aux trous d’aliasing du framebuffer réduit.
- **PANO-VECTOR-EDGE** : les contours restent tracés par QPainter avec antialiasing à la résolution finale ; le remplissage seul reste rasterisé.
- **PANO-AA** : `SmoothPixmapTransform` est explicitement maintenu lors de la remontée du framebuffer de profondeur vers la résolution de l’overlay.
- **ANNOTATIONS** : labels et grille de calibration sont dessinés après les contours physiques lorsque le z-buffer panoramique est actif.

## Architecture

Toujours deux moteurs seulement :

- PINHOLE : inchangé ;
- PANORAMA : pipeline commun EQUIRECT/CYLINDRICAL, avec seul le mapping angulaire spécifique à la projection.

## Non-régression

Aucune modification des fichiers UI ni du projecteur PINHOLE. Les fonctions principales du moteur PINHOLE restent identiques à 40.18.9.


---

# QCALVIEW 40.18.9 — moteur PANORAMA : clipping angulaire, mesh caché et z-buffer allégé

Base : **40.18.7** (garde mémoire / motifs stables). Les essais de tessellation lourde de 40.18.8 ne sont pas repris tels quels.

## Moteur panoramique commun

EQUIRECT et CYLINDRICAL utilisent le même pipeline :

1. géométrie monde / mesh ;
2. transformation batch vers angles caméra `alpha / beta / distance radiale` ;
3. clipping vertical en espace angulaire ;
4. subdivision locale uniquement pour les faces réellement problématiques ;
5. mapping EQUIRECT ou CYLINDRICAL ;
6. couture 0/360 commune ;
7. painter ou z-buffer radial commun.

La seule spécificité entre les deux projections reste le mapping `angle -> pixel` et leur domaine vertical mathématique.

## Correction des étirements au tangage

- Les surfaces ne sont plus écrasées contre la limite verticale CYLINDRICAL.
- Le `tan(beta)` n'est appelé qu'après clipping au VFOV cylindrique.
- En EQUIRECT, les surfaces remplies sont coupées quelques pixels avant la singularité des pôles, où l'azimut devient indéfini.
- Les longs contours sont subdivisés de façon bornée et coupés dans le même domaine angulaire avant leur projection.
- La couture 360 existante reste utilisée après clipping.

## Performance

- Cache des géométries transformées par FID, indépendant du pitch/roll.
- Cache de triangulation des anneaux BATI, invalidé par la révision de couche.
- Projection des sommets d'un mesh vers `alpha/beta/depth` effectuée en batch une fois par mesh.
- Subdivision limitée aux triangles grands / proches d'une singularité, profondeur faible et budget global par frame.
- Pas de création du gros calque temporaire plein format utilisé dans l'essai 40.18.8.

## Z-buffer panoramique

Quand « occultation objets » est active :

- toitures, murs, surfaces et billboards schématiques sont soumis au même z-buffer ;
- profondeur = distance radiale caméra-fragment ;
- résolution interactive du depth-buffer limitée à environ 2,5 / 5 / 8 Mpx (faible / normal / haute qualité) ;
- le rendu photo / overlay final conserve sa résolution demandée ;
- le framebuffer est libéré immédiatement après composition ;
- fallback Painter far-to-near si l'allocation du rasterizer échoue.

Le relief MNT conserve encore son système d'horizon propre : il n'est pas rasterisé dans le z-buffer objet de cette version.

## Motifs

- Les garde-fous 40.18.7 sont conservés.
- Sans z-buffer, la densité haute qualité peut toujours atteindre le budget généreux de 40.18.7.
- Avec z-buffer interactif, un plafond spécifique évite de rasteriser simultanément des dizaines de milliers de billboards texturés à pleine charge.
- Les billboards utilisent le même clipping et la même profondeur que les surfaces BATI.

## Non-régression

- moteur PINHOLE `_render_vector_layers_fast()` inchangé ;
- z-buffer PINHOLE `_compose_faces_zbuffer()` inchangé ;
- `projector.py` inchangé ;
- fichiers `.ui` inchangés.


---

# QCALVIEW 40.18.7 — stabilisation des motifs panoramiques

Base : **40.18.6**. Cette version vise le crash natif observé dans `project_points_batch()` lors du rendu de milliers de billboards panoramiques.

## PANO-SMALL-SCALAR
Les billboards EQUIRECT/CYLINDRICAL (4 sommets) ne passent plus par les masques et affectations NumPy de `project_points_batch()`. Leur projection, profondeur radiale et gestion de couture sont calculées en scalaire Python/math. Les grands ensembles (MNT, wireframe, batchs de géométrie) conservent NumPy.

## PANO-INSTANCE-BUDGET
Un budget global de billboards est appliqué **par frame de preview**, et non plus uniquement `max_instances` par entité. Valeurs nominales : 14 000 / 35 000 / 60 000 selon qualité, avec réduction supplémentaire uniquement en Memory Guard critique.

La densité élevée reste donc possible :
- une preview haute qualité peut afficher jusqu'à 60 000 instances ;
- l'export n'est pas plafonné par ce budget interactif ;
- le `max_instances` propre au style reste respecté par entité.

## CULL-BEFORE-BILLBOARD
Les instances trop lointaines ou sous-pixel sont rejetées avant échantillonnage Z et avant création du `Billboard3D`. Le seuil est conservateur et commun aux deux projections panoramiques.

## MEMORY-GUARD PROCÉDURAL
Le préflight estime désormais les **instances procédurales potentielles** à partir du symbole, de `max_instances`, du mode polygon/line et du nombre d'entités. En mode sécurisé il peut imposer un budget motifs temporaire, sans modifier le projet.

## SCHEDULER
- Faible latence désactivée : un seul rendu debouncé à la qualité demandée ; suppression du second rendu HQ caché.
- Faible latence activée : aperçu low puis éventuel HQ différé ; le HQ est supprimé si le Memory Guard a détecté un état critique/dangereux.

## Architecture
Toutes ces protections sont communes à **EQUIRECT et CYLINDRICAL**. Aucun renderer panoramique spécifique n'est ajouté. Le moteur PINHOLE et les fichiers `.ui` ne sont pas modifiés.


---

# QCALVIEW 40.18.6 — stabilisation panoramique conservatrice

Cette version repart explicitement de **40.18.4**. Les changements expérimentaux de 40.18.5 (tessellation adaptative massive et caches vectoriel/topographie séparés) ne sont pas repris.

## MEM-GUARD — protection avant allocation
Le moteur panoramique commun EQUIRECT/CYLINDRICAL dispose d'un préflight mémoire avant chaque aperçu interactif lourd.

Le garde estime notamment :
- RAM disponible et mémoire du processus QGIS ;
- résolution demandée et coût des QImage/QPixmap intermédiaires ;
- complexité approximative des couches visibles ;
- mode topographique, portée et pas du wireframe / balayage radial.

En charge critique, une boîte de dialogue propose :
- **Appliquer le mode sécurisé** (choix par défaut) ;
- **Continuer quand même** ;
- **Annuler le rendu**.

Le mode sécurisé est temporaire : il ne modifie ni le projet ni les valeurs des widgets. Il peut réduire l'aperçu, plafonner temporairement la portée, augmenter le pas topographique et borner davantage le nombre d'entités par couche.

## PURGE-BEFORE-ALLOC — pas d'accumulation d'overlays entre deux tangages
Pour EQUIRECT/CYLINDRICAL, les overlays rendus d'une ancienne orientation ne sont plus conservés dans `_overlay_cache` pendant la manipulation de la caméra.

Avant toute nouvelle grosse allocation :
- cache d'overlays panoramiques vidé ;
- ancien `overlay_image` relâché ;
- ancien `last_preview` relâché ;
- anciennes variantes 25/50/100 % du cache de base purgées (seule la taille courante est conservée) ;
- overlay de la visionneuse libéré ;
- en état critique/dangereux, ancien cache horizon libéré avant reconstruction.

À 100 %, la visionneuse réutilise l'overlay déjà calculé si ses dimensions sont identiques, au lieu de lancer une seconde construction pleine résolution.

## PANO-NATIVE-SAFE 2 — couture sans recast NumPy par primitive
Les helpers de couture 0/360 ne refont plus systématiquement `np.asarray(...)` sur des triangles/quads déjà stockés en `ndarray`.

En particulier `_viewport_shift_indices()` et `_small_finite_x_stats()` travaillent directement sur les petites primitives existantes. Cela cible le crash 40.18.4 observé dans `np.asarray(base)` sous forte pression native.

## SAFE-TOPO — limitation temporaire du relief
En mode sécurisé uniquement :
- wireframe : pas minimal adaptatif ;
- skyline/ridgelines/opaque : pas radial minimal ;
- portée panoramique plafonnée si nécessaire.

L'ancien horizon est libéré avant reconstruction lors d'un changement d'orientation panoramique, afin d'éviter le pic mémoire « ancien + nouveau cache ».

## Architecture
Toutes les protections ci-dessus sont communes à :
- EQUIRECT / EQUIRECTANGULAR ;
- CYLINDRICAL.

Il reste un seul **moteur PANORAMA** ; seule la formule de mapping angle → pixel diffère. Aucun chemin de sécurité propre à l'une des deux projections n'est introduit.

## Non-régression
- base de code : 40.18.4 ;
- aucune tessellation 40.18.5 réintroduite ;
- aucun cache vectoriel/topographique séparé réintroduit ;
- moteur PINHOLE non modifié ;
- `projector.py` non modifié ;
- fichiers `.ui` non modifiés ;
- raccord 0/360 conservé.


---

# QCALVIEW 40.18.4 — stabilité wireframe panoramique et continuité des volumes

## Correctifs principaux

### SAFE-WIREFRAME — pas fin sans grille dense géante
Le wireframe panoramique EQUIRECT/CYLINDRICAL bascule sur une génération adaptative sparse lorsque la grille de base dépasserait 1,2 million de cellules.

Cas de test visé : 8 km / pas 5 m.
- ancien chemin : grille de base 3201 × 3201 (>10,2 M cellules) et plusieurs matrices temporaires de même taille ;
- nouveau chemin panoramique : seuls les nœuds réellement retenus par le masque adaptatif sont matérialisés (~44 k avec D_adapt=500 m), puis échantillonnés/projetés en batch.

Le masque et les connexions restent équivalents au maillage adaptatif historique. Le chemin PINHOLE normal n'est pas modifié.

### PANO-NATIVE-SAFE — petites primitives sans réductions NumPy répétées
Les tests de validité des triangles/quads panoramiques et les statistiques de couture utilisent des contrôles scalaires bornés au lieu de `np.all`, `np.nanmin`, `np.nanmax`, `np.linalg.norm`, etc. dans les hot paths par primitive.

Objectif : éviter les access violations natives observées sous QGIS 3.44/Qt5 lors de scènes 360° très denses et sous forte pression mémoire.

### PANO-FACE-ORDER — murs/toitures ordonnés par profondeur radiale
Les murs et toitures d'un volume panoramique ne sont plus peints selon la règle fixe « tous les murs puis le toit ».

Chaque triangle porte déjà sa profondeur radiale. Les faces d'un même volume sont maintenant triées loin → proche avant peinture. Les triangles de murs sont peints indépendamment, ce qui évite aussi les annulations liées au remplissage odd-even d'un grand `QPainterPath` regroupant des faces qui se recouvrent.

Cette étape est directement compatible avec le futur z-buffer panoramique global.

## Architecture panoramique
Les corrections sont communes à :
- EQUIRECT / EQUIRECTANGULAR ;
- CYLINDRICAL.

Elles partagent les mêmes primitives, profondeur radiale, gestion de couture, maillage sparse, tri de faces et rendu. Seule la formule angle → pixel reste spécifique à la projection.

## Non-régression
- moteur vectoriel rapide PINHOLE inchangé ;
- `projector.py` inchangé ;
- interface inchangée ;
- raccord 0/360 inchangé ;
- motifs panoramiques et masse végétale inchangés hors sécurisation des primitives communes.


---

# QCALVIEW 40.18.3 — stabilisation panoramique scènes denses

- Correctif commun EQUIRECT/CYLINDRICAL : suppression de `np.nanmin/nanmax/nanmean` dans le hot-path de couture des primitives 3/4 sommets.
- Les bornes périodiques sont calculées par une réduction scalaire bornée, avec rejet propre des primitives non finies.
- Billboards panoramiques : itération sans matérialiser inutilement toutes les copies périodiques.
- Cache panoramique borné des `QSvgRenderer` et `QImage` : un motif répété ne recrée plus un objet Qt natif pour chaque instance.
- Le moteur PINHOLE et ses chemins z-buffer ne sont pas modifiés.
- La logique est strictement commune aux deux projections panoramiques ; seule la formule angle→pixel reste spécifique au mapping caméra.


---

# QCALVIEW 40.18.2 — moteur panoramique commun : surfaces triangulées

Cette version poursuit exclusivement la stabilisation des projections panoramiques.

## Architecture : un seul moteur panoramique

- **EQUIRECT** et **CYLINDRICAL** passent désormais par la même chaîne de rendu pour les géométries vectorielles panoramiques.
- La seule différence autorisée entre les deux modes est la **fonction de projection angle → pixel**.
- Couture 360°, triangulation, construction des murs/toitures, profondeur radiale, budgets de tessellation et composition QPainter sont communs.
- Le moteur **PINHOLE** reste séparé et inchangé.

Architecture visée :

1. moteur **PINHOLE** ;
2. moteur **PANORAMA**, recevant un mapping EQUIRECT ou CYLINDRICAL.

## STAB-PANO — stabilité du densificateur

- Le chemin scalaire panoramique n'utilise plus `float('nan')` / `float('inf')` dans sa boucle chaude ; il utilise `math.nan` / `math.inf`.
- EQUIRECT et CYLINDRICAL utilisent les **mêmes seuils de tessellation**.
- Ajout d'un **budget global de tessellation par frame** pour empêcher une scène dense de donner un budget maximal indépendant à chaque géométrie.
- Les coordonnées hors viewport restent finies dans le mapper panoramique quand cela est possible : le clipping est délégué au viewport plutôt que de provoquer des subdivisions répétées aux limites du FOV.
- Les polygones ne passent plus par le densificateur segment-par-segment qui était impliqué dans les access violations 40.18/40.18.1.

## POLY-Z — correction réelle du drapage

- `force_horizontal` est maintenant effectivement respecté par `_effective_base_z_array()`.
- Polygone simple : altitude MNT **sommet par sommet**.
- Volume 2,5D polygonal : plan de base horizontal historique, puis extrusion.
- Les valeurs MNT ponctuellement non finies utilisent la moyenne locale pour ne pas invalider une surface complète.
- Les appels PINHOLE continuent de demander explicitement le plan horizontal et conservent donc leur comportement.

## PANO-VOLUME — bâtiments et surfaces triangulés

L'ancien rendu panoramique faisait :

`anneau projeté → QPolygonF unique`

Il est remplacé pour les polygones panoramiques par :

`anneau monde → triangulation XY → projection → triangles panoramiques`

- Les toitures/surfaces sont triangulées en coordonnées monde avant projection.
- Les parois d'un volume sont converties en **deux triangles par segment**.
- Les anneaux QGIS sont nettoyés (fermeture dupliquée et doublons consécutifs).
- Le raccord 0/360 validé en 40.18.1 reste pris en charge au niveau des triangles.
- Les motifs/remplissages sont peints sur les triangles sans tracer leurs arêtes internes.
- Un objet traversant la couture ne génère plus une gigantesque texture temporaire couvrant tout le panorama.

## DEPTH-READY — préparation du z-buffer panoramique

Chaque triangle panoramique porte déjà :

- coordonnées écran `UV` ;
- coordonnées `XYZ` monde ;
- **profondeur radiale par sommet** ;
- UV texture optionnelles ;
- rôle de face (`roof`, `wall`, `ground_surface`, etc.).

Le rendu 40.18.2 utilise encore QPainter pour la composition. La prochaine étape pourra brancher ces mêmes faces sur le rasterizer/z-buffer panoramique sans reconstruire la géométrie.

## Projections aux limites du viewport

Pour les surfaces panoramiques, la projection peut produire des UV hors image mais finis. Le viewport effectue ensuite le clipping. Ce comportement est commun à EQUIRECT et CYLINDRICAL et évite la disparition complète d'une toiture lorsqu'un seul sommet dépasse légèrement le cadre cylindrique.

## Non-régression PINHOLE / interface

Vérifications réalisées :

- `_render_vector_layers_fast()` : identique à 40.18.1 ;
- `_compose_faces_zbuffer()` : identique à 40.18.1 ;
- `_draw_ztested_segment()` : identique à 40.18.1 ;
- `_project_uv_depth_small()` : identique à 40.18.1 ;
- `projector.py` : identique à 40.18.1 ;
- fichiers `ui/` : identiques à 40.18.1.

## Tests techniques hors QGIS

- compilation Python complète ;
- création de surfaces et murs pour EQUIRECT et CYLINDRICAL avec la même API ;
- profondeur radiale identique pour une même géométrie dans les deux mappings ;
- triangle traversant 0/360 dupliqué uniquement dans les copies locales utiles ;
- projection cylindrique hors FOV conservée sous forme d'UV finis pour clipping ultérieur.


---

# QCALVIEW 40.18.1 — stabilisation géométrique panoramique

Cette version poursuit uniquement le chantier **EQUIRECT / CYLINDRICAL**. Le moteur rapide **PINHOLE** et les fichiers d’interface ne sont pas modifiés.

## PANO-1 — polygones drapés

- Les polygones panoramiques simples, non extrudés, utilisent désormais les altitudes MNT sommet par sommet.
- Le comportement horizontal historique est conservé pour les volumes 2,5D réellement extrudés (bâtiments, plateformes, etc.).
- Cela supprime l’effet de « plaque horizontale » particulièrement visible sur les vues drone plongeantes.

## PANO-2 — couture 0/360 des anneaux fermés

- Le sommet de fermeture dupliqué est retiré avant le déroulage horizontal.
- L’anneau est refermé dans la même copie périodique de l’image.
- Les copies nécessaires sont limitées au viewport [0,W], ce qui empêche une arête artificielle de largeur quasi complète du panorama.
- La même logique est utilisée par les primitives schématiques panoramiques et les billboards.

## PANO-3 — tessellation itérative bornée

- `_densify_projected_segment()` n’est plus récursif.
- Suppression des créations répétées de petits tableaux NumPy à chaque niveau de subdivision.
- Projection scalaire des points de contrôle et pile itérative explicite.
- Budgets maximum par segment et par chemin, avec dégradation contrôlée vers la géométrie source lorsqu’un budget est atteint.
- Objectif : éviter les access violations observées sur QGIS 3.44 dans les scènes denses.

## PANO-4 — primitives panoramiques communes

- Nouveau module `core/_panorama_primitives.py`.
- Une primitive projetée peut maintenant porter ensemble :
  - coordonnées écran UV ;
  - profondeur radiale caméra ;
  - géométrie XYZ monde ;
  - coordonnées UV de texture optionnelles ;
  - informations de fermeture/rôle.
- Les lignes/polygones panoramiques du renderer historique utilisent déjà cette structure.
- Les primitives schématiques panoramiques l’utilisent également.
- Les billboards SVG/PNG stockent dès maintenant leurs UV texture `[0..1]`, en préparation du futur rasterizer texturé + z-buffer.

## Motifs / stabilité

- Un motif schématique panoramique qui échoue ne retombe plus silencieusement sur le rendu lourd du polygone source.
- Les billboards traversant la couture 360 sont rendus dans des copies locales et ne peuvent plus créer un rectangle de presque toute la largeur de l’image.
- Le rendu QPainter reste provisoire pour les textures ; leur déformation fine sera traitée lors du passage au rasterizer z-buffer panoramique.

## Profondeur

- PINHOLE conserve exactement la profondeur historique `yc`.
- EQUIRECT/CYLINDRICAL utilisent désormais une profondeur radiale `sqrt(dx²+dy²+dz²)` dans les helpers communs, indispensable à un futur z-buffer 360°.

## Vérifications de non-régression

- `_render_vector_layers_fast()` (moteur PINHOLE) identique à 40.18.
- `projector.py` inchangé.
- Tous les fichiers `ui/` inchangés.
- Compilation Python complète validée.
- Tests synthétiques validés : anneau traversant 0/360, densification d’un segment traversant la couture, ring dense borné, profondeur PINHOLE vs profondeur radiale panoramique.


---

# QCALVIEW 40.18 — fondations panoramiques A/B/C

Version ciblée sur EQUIRECT / CYLINDRICAL. Le moteur de rendu PINHOLE et la disposition de l'interface ne sont pas modifiés.

## A — Wireframe panoramique
- Restaure la différence fonctionnelle entre `Filaire complet` et `Arêtes supérieures` pour les projections panoramiques.
- `Arêtes supérieures` filtre désormais les nœuds/segments du DEM masqués par du relief plus proche.
- Le test est vectorisé en NumPy à partir du cache radial d'horizon (`el_cummax`) : aucun rappel massif Python/QGIS/SIP n'est réintroduit dans la boucle du filaire.
- PINHOLE conserve son chemin historique inchangé.

## B — 360° / couture panoramique
- HFOV accepte désormais explicitement `360.00°` sans nouveau contrôle ni modification de disposition UI.
- Un HFOV de 360° active le mode périodique horizontal pour EQUIRECT et CYLINDRICAL, en plus de la case 360° historique.
- EQUIRECT 360° reste forcé à 360×180 ; CYLINDRICAL 360° conserve son VFOV propre.
- Synchronisation du mode 360 effectif avec le style PDV, les états caméra, les offsets, la couture des lignes et les opérations panoramiques auxiliaires.
- Suppression des traits traversant toute l'image lorsqu'une géométrie franchit la couture 0°/360°.

## C — Courbure correcte des géométries panoramiques
- Les lignes et contours polygonaux du moteur panoramique historique sont densifiés adaptativement avant projection.
- La subdivision est pilotée par l'erreur écran en pixels et tient compte de la couture 360°.
- Les contours, dessus de volumes et parois 2,5D suivent désormais la courbure propre à EQUIRECT/CYLINDRICAL au lieu de relier seulement les sommets par des cordes droites.
- Le contrat d'altitude existant des polygones/volumes est volontairement conservé dans cette version : 40.18 corrige la projection, pas la sémantique Z.

## Hors périmètre
- Z-buffer panoramique.
- Motifs SVG/billboards dans le z-buffer panoramique.
- Refonte de la sémantique polygone drapé / volume horizontal.


---

# QCALVIEW 40.17.5

## Export et calage

- Corrige les offsets en mode **Pixels** : la valeur est désormais exprimée dans les pixels de l’image pleine résolution, quel que soit le niveau d’aperçu (25 %, 50 %, 100 %).
- L’aperçu à 25 % et l’export pleine résolution utilisent donc le **même calage visuel**.
- Le tableau de l’onglet Export affiche l’état effectif du PDV, brouillons compris : projection, azimut, tangage, roulis, HFOV, VFOV et offsets H/V.
- Le batch exporte désormais l’état effectif affiché (WYSIWYG) au lieu d’ignorer silencieusement les brouillons et de revenir aux anciens attributs persistés.
- Un brouillon est clairement signalé par l’état **Brouillon** ; l’utilisateur peut l’exporter sans le persister, ou cliquer sur **Enregistrer paramètres + état** pour l’écrire dans la couche PDV.
- Après un enregistrement réussi, le brouillon est supprimé afin que l’état repasse correctement à **OK**.

## Compatibilité

- Conserve les corrections 40.17.4 sur le mapping UV des motifs.
- Branche dual-compatible QGIS 3.44 / QGIS 4.x.


---

# QCALVIEW 40.17.4

## Correction des motifs SVG/PNG coupés en PINHOLE

La 40.17.4 corrige le placage des textures des billboards schématiques dans le z-buffer logiciel.

### Cause

Jusqu'à la 40.17.3, le SVG/PNG était étiré dans le rectangle englobant horizontal/vertical du billboard projeté. Le rendu était ensuite limité aux deux triangles du quadrilatère projeté. Dès que le calage (pitch/roll/orientation) transformait le billboard en trapèze ou parallélogramme, les triangles coupaient donc une partie de la texture suivant leur diagonale.

### Correctif

- coordonnées UV explicites aux quatre coins de chaque billboard ;
- interpolation barycentrique des UV sur chacun des deux triangles ;
- interpolation perspective-correcte à partir de la profondeur caméra `yc` ;
- conservation du clipping viewport et du z-buffer commun ;
- le mapping historique par `pattern_bbox` reste inchangé pour les motifs de remplissage QGIS ordinaires ;
- conservation du safe padding SVG introduit en 40.17.3 comme protection secondaire contre les SVG dont le tracé touche leur `viewBox`.

### Effet attendu

Les animaux, véhicules, arbres et autres billboards ne doivent plus être amputés par une droite diagonale lorsque les paramètres de calage varient. Les occultations réelles par des objets situés devant restent gérées par le z-buffer.


---

# QCALVIEW 40.17.3 — safe padding SVG renforcé

## Correctif

La 40.17.2 modifiait le `viewBox` du `QSvgRenderer` après chargement. Le test vidéo montre que ce garde-fou n’est pas suffisant pour certains SVG dont les tracés arrivent sur la limite du document.

La 40.17.3 applique deux protections :

- expansion du `viewBox` racine directement dans les octets SVG **avant** le chargement par Qt (3,5 % par côté) ;
- petite garde transparente supplémentaire au moment de la rasterisation PINHOLE (1 à 3 pixels dans la texture interne), afin qu’un pixel antialiasé ne tombe jamais exactement sur la bordure de texture.

Le chemin direct QPainter (projections panoramiques) utilise lui aussi le renderer préchargé avec `viewBox` élargi.

## Compatibilité

- QGIS 3.44 / Qt5 : `QSvgRenderer.load(QByteArray)` ;
- QGIS 4.x / Qt6 : même API ;
- fallback conservé vers le chargement fichier + `setViewBox()` si un SVG n’a pas de `viewBox` racine exploitable.


---

# QCALVIEW 40.17.2 — safe padding des motifs SVG

Branche basée strictement sur la 40.17.1.

- Ajout d’un **safe padding SVG de 2,5 % par côté** au niveau du `viewBox` logique avant rendu Qt.
- Objectif : éviter le rognage des silhouettes dont les tracés/contours touchent ou dépassent légèrement le `viewBox` déclaré (tracteurs, animaux, silhouettes importées, etc.).
- Correction appliquée aux deux chemins de rendu des billboards schématiques :
  - rasterisation SVG du moteur PINHOLE / z-buffer ;
  - rendu direct utilisé par les projections panoramiques.
- Le padding est appliqué uniquement aux SVG ; les PNG ne sont pas modifiés.
- Le cache des textures PINHOLE est versionné pour ne pas réutiliser une texture rasterisée sans padding.
- Aucun SVG de la bibliothèque n’est réécrit : la correction est défensive et fonctionne aussi avec les motifs SVG ajoutés par l’utilisateur.
- Compatibilité visée : QGIS 3.44 / Qt5 et QGIS 4.x / Qt6.


---

# QCALVIEW 40.17.1 — ergonomie Couches projetées

Branche basée strictement sur la 40.17.

- Double-clic sur une ligne du tableau « Couches projetées » : ouvre le même dialogue Style que le bouton « Style… ».
- Ajout dans le menu contextuel des couches vectorielles de l'arborescence QGIS d'une seule action « Ajouter à QCALVIEW ».
- Si le dock QCALVIEW n'est pas encore créé, l'action l'initialise et l'affiche avant l'ajout.
- Une couche déjà présente n'est pas dupliquée.
- L'action contextuelle est retirée proprement au déchargement du plugin.
- Aucun bouton existant n'est supprimé et aucune autre modification fonctionnelle ou d'interface n'est introduite.
- Compatibilité visée : QGIS 3.44 / Qt5 et QGIS 4.x / Qt6.


---

# QCALVIEW 40.17 — outils modaux et référentiel Z AVR

- Suspend les QTimer/rendus QCALVIEW pendant le calcul de hauteur d’occultation ouvert depuis le dialogue de style.
- Empêche les boucles d’événements imbriquées Qt de relancer un rendu sur les couches pendant que l’outil les lit ou les modifie.
- Reprend un seul rendu après fermeture de l’outil.
- Convertit le référentiel Z utilisé par les générateurs AVR en tuples Python immuables et scalaires ; `ground_z()` ne conserve plus de vues NumPy/QGIS.
- Conserve tous les correctifs de la 40.16 et ne modifie pas l’interface ni les choix de rendu.


---

# QCALVIEW 40.16 — compatibilité QGIS 3.44 / QGIS 4

Branche issue directement de **40.14**. Les changements sont limités à la stabilité et à la frontière Qt/NumPy ; aucune modification d’interface, de paramètres AVR ou de géométrie métier.

## Correctifs

- Suppression de toute vue NumPy modifiable sur la mémoire interne d’un `QImage`. Les conversions QImage → NumPy passent d’abord par une copie Python propriétaire.
- Le framebuffer PINHOLE reste désormais détenu par NumPy jusqu’après le z-buffer **et** le masque d’occultation MNT ; la conversion en `QImage` n’est faite qu’une seule fois à la fin.
- Conversion NumPy → `QImage` depuis un buffer `bytes` autonome, jamais depuis `ndarray.data`.
- `_significant_ring_vertex_indices`, `_wall_visibility_flags_xy` et `_opaque_vertical_corner_indices` utilisent des calculs scalaires Python (`math`) pour les petits anneaux ; suppression de `np.dot`, `np.clip`, `np.hypot` et `np.allclose` dans ce chemin.
- Projection scalaire dédiée aux micro-primitives (≤16 points), avec API de sortie inchangée. Les gros lots restent vectorisés avec NumPy.
- Clipping et préparation des triangles du z-buffer en tuples Python ; NumPy est réservé aux tuiles raster, où la vectorisation est réellement utile.
- Tests de LOD et de finitude des petites primitives AVR passés en calcul scalaire.
- Conversion de textures et recadrage des aperçus : aucune vue ndarray ne référence directement la mémoire Qt.
- Correction scalaire du calcul d’orientation des lignes de tables PV dans `_schematic_symbols.py`.

## Invariants conservés

- mêmes paramètres et familles AVR ;
- même géométrie des panneaux, pieds, haies, clôtures et arbres ;
- même moteur PINHOLE et même z-buffer ;
- même occultation MNT ;
- mêmes réglages 25/50/100 % et Faible latence ;
- compatibilité annoncée QGIS 3.44/Qt5 et QGIS 4.x/Qt6.


---

# QCALVIEW 40.14 — correctifs ciblés

- Z-buffer PINHOLE : framebuffer RGBA désormais détenu par NumPy pendant le calcul puis copié vers un `QImage` autonome en fin de composition. Suppression de l’écriture NumPy directe dans `QImage.bits()` pour le framebuffer principal.
- Faces procédurales PV : calcul des bounding boxes par boucle scalaire finie au lieu de `np.nanmin/np.nanmax` sur des petits anneaux répétés des dizaines de milliers de fois.
- Tables PV polygonales : suppression du `np.nanmin` résiduel pour le calcul de l’axe local.
- Bouton Actualiser : annulation des timers de rendu différé encore en attente avant le rendu forcé, afin d’éviter des rendus lourds obsolètes en cascade.
- Lignes 2,5D : le pied suit toujours le profil MNT sommet par sommet. L’horizontalisation reste réservée aux volumes polygonaux.
- AVR linéaires (haies, clôtures, etc.) : même profil Z contractuel que le rendu géométrique linéaire.
- Aucun changement d’interface, de bibliothèque AVR, de paramètres PV ou de projection.


---

# QCALVIEW 40.13 — cohérence de positionnement

Correctif strictement ciblé sur le placement des objets.

- Un seul échantillonneur MNT pour les appels scalaires et batch.
- Le rendu PINHOLE calcule une fois le profil Z contractuel de chaque entité.
- Le même profil est transmis sans rééchantillonnage aux AVR (haies, clôtures, végétation, SVG/images, PV, etc.).
- Les polygones conservent le même plan de base horizontal que le rendu classique.
- Les lignes conservent le même profil terrain que le rendu classique ; si l’option de base horizontale 2,5D est active, l’AVR reçoit également cette base horizontale.
- La clôture générique respecte désormais une hauteur spécifique définie au niveau de la couche, comme la haie générique.

Aucune modification d’interface, de projection ou de logique de profondeur.


---

# QCALVIEW 40.12.2 — correctif ciblé

- Corrige `IndexError: list index out of range` dans `_opaque_vertical_corner_indices`.
- Normalise les anneaux polygonaux pouvant contenir plusieurs sommets de fermeture identiques.
- Empêche une seconde suppression de fermeture dans `_wall_visibility_flags_xy`.
- Ajoute une garde défensive si le nombre de drapeaux de visibilité ne correspond pas au nombre de sommets.
- Aucun changement d’interface, de style AVR, de projection ou de logique métier.


---

# QCALVIEW 40.12 — correctifs ciblés

Branche issue directement de 40.11. Aucun changement d’interface ou de choix fonctionnel.

- état AVR atomique : un AVR n’est actif que si un motif valide est réellement sélectionné ; les anciens états incomplets sont neutralisés ;
- états AVR invalides restaurés depuis un PDV : retour propre au rendu géométrique classique au lieu de masquer la couche ;
- sécurisation des sélections d’indices sur tableaux vides (`list index out of range`) ;
- triangulation PINHOLE : stockage scalaire des triangles pour supprimer les dizaines de milliers de petits tableaux NumPy conservés simultanément ;
- référentiel Z unique entre rendu classique et générateurs AVR : polygones sur le même plan moyen que le classique, lignes sur le même profil interpolé entre sommets ;
- demandes de rendu coalescées par génération : pas de file de rendus obsolètes derrière un rendu en cours ;
- journalisation ciblée couche/FID/motif en cas d’échec AVR.


---

# QCALVIEW 40.11 — stabilisation PINHOLE

Correctif ciblé à partir de `QCALVIEW_v40_10_JALON_OK.zip`.

## Modifications

- le scheduler adaptatif ne modifie plus silencieusement le choix utilisateur 25 / 50 / 100 % ;
- le passage différé haute qualité reste à la résolution d'overlay choisie ;
- le z-buffer PINHOLE découpe les triangles sur le viewport puis les rasterise par tuiles de 192 px, au lieu d'allouer un `meshgrid` sur toute leur emprise ;
- le masque d'occultation par le relief est calculé par tuiles de 256 px ;
- les segments z-testés sont clippés sur l'image avant échantillonnage de profondeur/horizon ;
- les constantes de projection PINHOLE utilisées par le test d'horizon sont mises en cache pour le rendu courant ;
- la triangulation ear-clipping évite les petites allocations NumPy répétées ;
- les haies AVR et clôtures AVR interpolent désormais le profil Z des sommets de la géométrie source : leur pied reste aligné avec le rendu ligne / extrusion de la même couche ;
- la visionneuse conserve l'overlay à sa résolution native et le mappe dans l'espace de la photo, évitant un agrandissement raster intermédiaire inutile ;
- en passage `high`, le z-buffer utilise 100 % de la résolution d'overlay choisie afin de limiter le flou sans forcer la photo à 100 %.

## Invariants conservés

Aucune refonte de l'interface, des catégories AVR, des paramètres métier, du principe de profondeur PINHOLE, des générateurs procéduraux ou des autres projections.


---

# QCALVIEW 40.10.2 — Live couches projetées

- Correction du mode **Live** : il ne surveillait que les changements de géométrie de la couche PDV/caméra.
- Les couches projetées déjà surveillées par QCALVIEW déclenchent désormais elles aussi l’actualisation Live sur `geometryChanged`, `featureAdded`, `featuresDeleted`, `attributeValueChanged`, changements validés, `dataChanged` et `rendererChanged`.
- Un seul timer d’antirebond de 250 ms est conservé pour le PDV et les couches projetées.
- Le bouton **Rafraîchir** et le moteur PINHOLE 40.9.1 restent inchangés.


---

# QCALVIEW 40.10.1 — correctif visionneuse PDV

- Correction du placement des méthodes `sync_pdv_controls`, `_refresh_from_owner`, `_navigate_pdv`, `_on_viewer_pdv_changed` et `_on_live_toggled` dans la classe `_ImageViewer`.
- Le bouton Rafraîchir, les flèches PDV, la liste déroulante et le mode Live sont désormais reliés à des méthodes existantes de la visionneuse.
- Le mode Live utilise `_camera_set_live_enabled()` lorsqu'il est disponible.
- Aucun changement du moteur de rendu PINHOLE 40.9.1.


---

# QCALVIEW 40.10 — workflow PDV paysagiste

- Style QCALVIEW de la couche PDV désactivé par défaut ; application uniquement sur demande et sur la couche explicitement choisie.
- CRS de travail : CRS projet projeté/métrique. Les couches source (dont PDV WGS84) sont transformées vers ce CRS avant rendu. Les projets en CRS géographique sont refusés avec avertissement.
- Calage azimut/tangage : pas visible 1° / 0,1° / 0,01°, 1° par défaut.
- Visionneuse : précédent/suivant, liste déroulante de tous les PDV, bouton Rafraîchir, mode Live.
- Live désactivé par défaut, rendu temporisé 250 ms après stabilisation de la géométrie du PDV courant.
- Champs PDV distincts : identifiant, libellé, ordre, image.
- Navigation vers un PDV en mode SCHEMA conserve la vue schématique et ne charge aucune photo.


---

# QCALVIEW 40.9 — moteur PINHOLE unifié

Base : **40.8**. Référence visuelle SVG PINHOLE : **40.7 JALON_PINHOLE_OK**.

## Objectif

Finaliser le moteur de représentation **PINHOLE** avant toute reprise des projections panoramiques.

## Architecture PINHOLE

- un seul chemin de rendu objet en PINHOLE ;
- profondeur/z-buffer toujours actif pour les faces et billboards ;
- la case historique `Masquage inter-objets` ne pilote plus le moteur PINHOLE ;
- l'opacité ne change jamais de renderer ;
- le mode extrusion 2.5D ne change jamais le renderer des motifs schématiques ;
- aucun fallback QPainter direct n'est autorisé pour les SVG/PNG schématiques en PINHOLE ;
- EQUIRECT/CYLINDRICAL conservent volontairement leur moteur historique pour un chantier ultérieur.

## SVG / PNG

- le rendu visuel 40.7 reste la référence, mais sa voie SVG directe ne validait pas `RGBA8888` comme surface intermédiaire ;
- rasterisation SVG sur `ARGB32_Premultiplied` (surface fiable pour `QSvgRenderer`), puis conversion RGBA uniquement pour le z-buffer ;
- conservation du support PNG introduit en 40.8 ;
- rasterisation transparente puis envoi du billboard au z-buffer commun ;
- base du billboard inchangée : `Z = Z terrain`, hauteur développée uniquement vers le haut ;
- transparence intrinsèque de l'image conservée ;
- les pixels transparents ne revendiquent aucune profondeur ;
- le seuil de profondeur ignore les franges d'antialiasing très faibles ;
- l'opacité de couche est esthétique : elle ne modifie pas la géométrie d'occlusion du motif ;
- l'alpha d'un remplissage QGIS de ligne/point ne peut plus rendre un billboard SVG invisible : l'opacité SVG suit la définition du motif et l'opacité de couche, comme dans le rendu direct 40.7.

## Z-buffer

- coordonnées de texture et `pattern_bbox` restent dans le même repère après réduction de résolution ;
- suppression du cas spécial qui forçait les textures en z-buffer pleine résolution ;
- même politique de framebuffer pour SVG/PNG, géométries procédurales et objets 2.5D ;
- masque d'horizon MNT 40.8 conservé comme étape supplémentaire du moteur PINHOLE.

## Bibliothèque / procédural

Conservés depuis 40.8 :

- listing Type → Famille → Motif ;
- sélection de plusieurs modèles (jusqu'à 3) pour une même couche ;
- alternance déterministe des modèles ;
- SVG et PNG ;
- végétation adaptative ;
- PV, éoliennes, clôtures et autres générateurs procéduraux ;
- interface Qt et paramètres 40.8.

## Compatibilité

Le widget historique `cb_occ_objects` est conservé en interne pour les états/projets existants et le futur chantier panoramique, mais masqué dans l'interface 40.9 car il n'a plus d'effet sur le choix du moteur PINHOLE.


## Correctif 40.9.1 — SVG PINHOLE + netteté

- correction du rasteriseur SVG PINHOLE : `QSvgRenderer.render()` utilise désormais `QRectF`, comme le renderer direct éprouvé ;
- suppression de la régression silencieuse qui déclenchait le fallback procédural lorsque l’overload Qt refusait `QRect` ;
- texture SVG/PNG rasterisée au ratio et à la taille projetée du billboard, au lieu d’une petite texture carrée ;
- tailles de textures regroupées par paliers pour réutiliser le cache et limiter les appels à `QSvgRenderer` ;
- moteur PINHOLE toujours unique : aucune réintroduction du QPainter direct ;
- lorsque la scène contient des billboards SVG/PNG, le même z-buffer fonctionne à pleine résolution jusqu’à 2200 px de côté (minimum 0,75 au-delà), afin d’éviter le flou du framebuffer low-latency agrandi ;
- aucun changement du moteur EQUIRECT/CYLINDRICAL.


---

# QCALVIEW 40.8 — PINHOLE z-buffer + AVR

Base de rendu : **40.7 JALON_PINHOLE_OK**.

## PINHOLE
- le compositeur z-buffer de la 40.7 est conservé à l’identique ;
- le moteur PINHOLE rapide reste utilisé quel que soit le mode Relief ;
- l’occlusion MNT devient un masque distance-aware appliqué après le z-buffer objets ;
- les arêtes différées (clôtures, lignes procédurales, pieds, etc.) utilisent aussi le test d’horizon lorsqu’un tampon de profondeur est disponible ;
- EQUIRECTANGULAR et CYLINDRICAL restent sur leur moteur panoramique existant.

## Motifs / AVR
- reprise de la bibliothèque hiérarchique 40.7.7 ;
- SVG et PNG, jusqu’à 3 modèles ;
- motifs procéduraux : végétation, clôtures, éoliennes, panneaux PV ;
- panneaux fixes, verticaux et trackers mono-axe ;
- pieds/supports métriques des panneaux ;
- moteur de composition AVR/z-buffer PINHOLE de la 40.7 non remplacé.

## Validation statique
- compilation Python complète ;
- JSON de bibliothèque validés ;
- générateurs référencés par la bibliothèque présents dans `_schematic_symbols.py`.

La validation visuelle finale doit être faite dans QGIS sur le projet de test utilisateur.

## Correctif rendu AVR PINHOLE
- correction du repère `pattern_bbox` lorsque le z-buffer est rendu à une échelle différente de 1 ;
- les faces SVG/PNG sont explicitement reconnues comme textures ;
- présence de textures AVR => z-buffer à l'échelle 1, y compris avec l'aperçu faible latence activé par défaut ;
- si un motif ne peut exceptionnellement pas être converti en primitives z-buffer, retour visuel au renderer AVR historique au lieu de retomber silencieusement sur le symbole QGIS de la couche.

## Correctif PINHOLE opacité + AVR (stabilisation)
- l'opacité d'une couche ne désactive plus le z-buffer PINHOLE ;
- retour au compositeur et à l'adaptateur procédural simples de la branche 40.7/40.8 précédente : suppression de la tentative de compositeur translucide et des UV texturés ajoutés dans le correctif précédent ;
- les couleurs des motifs procéduraux ne reçoivent plus l'opacité deux fois : `schematic_role_colors()` reste l'unique source d'alpha ;
- les billboards SVG/PNG reçoivent la même opacité que le renderer AVR historique ;
- rasterisation intermédiaire des SVG/PNG sur `ARGB32_Premultiplied`, format fiable pour QPainter/QSvgRenderer sous Qt5 et Qt6, puis conversion RGBA uniquement pour le z-buffer ;
- les trous transparents des SVG/PNG ne revendiquent toujours aucune profondeur ;
- aucun changement du moteur EQUIRECTANGULAR/CYLINDRICAL.
