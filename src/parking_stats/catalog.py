"""The language catalog: the only place that names a language (td423).

Each language is one block in ``LANGUAGES``:

- ``code``: the language code used for ``lang``, ``hreflang`` and page names;
- ``name``: its own name (endonym), which the language picker shows;
- ``dir``: ``rtl`` or ``ltr``;
- ``default``: exactly one language is the source; its pages keep the
  original file names (``index.html``, ``heatmap.html``);
- ``machine_draft``: True while the text is an unreviewed machine draft;
- ``plural``: a plural-rule family from ``i18n.PLURAL_FAMILIES``.

``STRINGS`` maps every key to one value per language. A value is either a
string or, for counted text, a dict of plural forms named by the
language's family. Values are trusted HTML fragments (they may hold
entities such as ``&middot;``); ``{name}`` placeholders are filled by the
caller with already-escaped values.

Adding a language is one new block plus one value per key; the tests in
tests/test_i18n.py fail until every key has it, and no other file names
languages.

Never translated (data, not UI): street, category and neighbourhood
names, plates, CSV/JSON field names and values, and the upstream
report.html.
"""

from __future__ import annotations

from typing import TypedDict


class Language(TypedDict):
    code: str
    name: str
    dir: str
    default: bool
    machine_draft: bool
    plural: str


LANGUAGES: tuple[Language, ...] = (
    {
        "code": "he",
        "name": "עברית",
        "dir": "rtl",
        "default": True,
        "machine_draft": False,
        "plural": "one_is_1",
    },
    {
        "code": "fr",
        "name": "Français",
        "dir": "ltr",
        "default": False,
        # Machine draft, needs native review.
        "machine_draft": True,
        "plural": "one_is_0_1",
    },
)

STRINGS: dict[str, dict[str, str | dict[str, str]]] = {
    # Shared
    "site.title": {
        "he": "דיווחי 106 - ירושלים",
        "fr": "Signalements 106 - Jérusalem",
    },
    "picker.label": {
        "he": "שפה",
        "fr": "Langue",
    },
    "machine_draft.notice": {
        "he": "תרגום מכונה, טרם נבדק על ידי דובר/ת שפת אם.",
        "fr": "Traduction automatique, pas encore relue par un locuteur natif.",
    },
    "label.default": {
        "he": "דיווחי 106 מצטברים",
        "fr": "Signalements 106 agrégés",
    },
    "analytics.privacy": {
        "he": "פרטיות: ספירת ביקורים אנונימית (Cloudflare Web Analytics) בלבד - "
        "ללא עוגיות וללא שמירת כתובות IP.",
        "fr": "Confidentialité : comptage anonyme des visites (Cloudflare Web "
        "Analytics) uniquement, sans cookies ni conservation des adresses IP.",
    },
    # Landing page (publish.py)
    "index.intro": {
        "he": "כל הנתונים כאן מצטברים בלבד: ספירות לפי חודש, שבוע, קטגוריה, רחוב\n"
        "ושכונה - ללא נתונים גולמיים או שמות. לוחיות רישוי מוצגות רק לרכבים\n"
        "שדווחו שלוש פעמים ומעלה, יחד עם מספר הדיווחים בלבד.",
        "fr": "Toutes les données présentées ici sont agrégées : décomptes par mois,\n"
        "semaine, catégorie, rue et quartier, sans données brutes ni noms. Les\n"
        "plaques d'immatriculation ne sont affichées que pour les véhicules\n"
        "signalés trois fois ou plus, avec le seul nombre de signalements.",
    },
    "index.monthly": {
        "he": "דיווחים לפי חודש (12 האחרונים)",
        "fr": "Signalements par mois (12 derniers)",
    },
    "index.categories": {
        "he": "קטגוריות מובילות",
        "fr": "Principales catégories",
    },
    "index.streets": {
        "he": "רחובות מובילים",
        "fr": "Principales rues",
    },
    "index.links": {
        "he": "הדוח המלא והנתונים",
        "fr": "Rapport complet et données",
    },
    "index.link.report": {
        "he": "הדוח המלא עם כל הגרפים",
        "fr": "Le rapport complet avec tous les graphiques (en hébreu)",
    },
    "index.link.summary_note": {
        "he": "(סיכום מכונה-קריא)",
        "fr": "(résumé lisible par machine)",
    },
    "index.link.monthly": {
        "he": "טבלה חודשית",
        "fr": "Tableau mensuel",
    },
    "index.link.categories": {
        "he": "קטגוריות",
        "fr": "Catégories",
    },
    "index.link.streets": {
        "he": "רחובות",
        "fr": "Rues",
    },
    "index.link.heatmap": {
        "he": "מפת חום של מוקדים חמים",
        "fr": "Carte de chaleur des points chauds",
    },
    "index.footer": {
        "he": "מקור הנתונים: {label} &middot; עודכן: {updated} &middot; "
        "נתונים מצטברים בלבד",
        "fr": "Source des données : {label} &middot; Mis à jour : {updated} "
        "&middot; Données agrégées uniquement",
    },
    "card.reports": {
        "he": "דיווחים",
        "fr": "Signalements",
    },
    "card.period": {
        "he": "תקופה",
        "fr": "Période",
    },
    "card.peak": {
        "he": "החודש העמוס",
        "fr": "Mois le plus chargé",
    },
    "card.median": {
        "he": "זמן טיפול חציוני",
        "fr": "Délai de traitement médian",
    },
    "duration.minutes": {
        "he": {"one": "כ-{n} דקות", "other": "כ-{n} דקות"},
        "fr": {"one": "environ {n} minute", "other": "environ {n} minutes"},
    },
    "duration.hours": {
        "he": {"one": "כ-{n} שעות", "other": "כ-{n} שעות"},
        "fr": {"one": "environ {n} heure", "other": "environ {n} heures"},
    },
    "duration.days": {
        "he": {"one": "כ-{n} ימים", "other": "כ-{n} ימים"},
        "fr": {"one": "environ {n} jour", "other": "environ {n} jours"},
    },
    "month.01": {"he": "ינואר", "fr": "janvier"},
    "month.02": {"he": "פברואר", "fr": "février"},
    "month.03": {"he": "מרץ", "fr": "mars"},
    "month.04": {"he": "אפריל", "fr": "avril"},
    "month.05": {"he": "מאי", "fr": "mai"},
    "month.06": {"he": "יוני", "fr": "juin"},
    "month.07": {"he": "יולי", "fr": "juillet"},
    "month.08": {"he": "אוגוסט", "fr": "août"},
    "month.09": {"he": "ספטמבר", "fr": "septembre"},
    "month.10": {"he": "אוקטובר", "fr": "octobre"},
    "month.11": {"he": "נובמבר", "fr": "novembre"},
    "month.12": {"he": "דצמבר", "fr": "décembre"},
    "plates.title": {
        "he": "רכבים חוזרים",
        "fr": "Véhicules récurrents",
    },
    "plates.intro": {
        "he": "רכבים שדווחו שלוש פעמים ומעלה; מוצגים מספר הרישוי ומספר הדיווחים בלבד.",
        "fr": "Véhicules signalés trois fois ou plus ; seuls le numéro "
        "d'immatriculation et le nombre de signalements sont affichés.",
    },
    "plates.col.plate": {
        "he": "מספר רישוי",
        "fr": "Immatriculation",
    },
    "plates.col.reports": {
        "he": "דיווחים",
        "fr": "Signalements",
    },
    "plates.count": {
        "he": {"one": "{count} דיווחים", "other": "{count} דיווחים"},
        "fr": {"one": "{count} signalement", "other": "{count} signalements"},
    },
    # Charts (charts.py)
    "chart.bars": {
        "he": {"one": "{count} עמודות", "other": "{count} עמודות"},
        "fr": {"one": "{count} barre", "other": "{count} barres"},
    },
    "chart.rows": {
        "he": {"one": "{count} שורות", "other": "{count} שורות"},
        "fr": {"one": "{count} ligne", "other": "{count} lignes"},
    },
    # Heat map page (heatmap.py)
    "heatmap.title": {
        "he": "מפת חום - דיווחי 106 ירושלים",
        "fr": "Carte de chaleur - Signalements 106 Jérusalem",
    },
    "heatmap.h1": {
        "he": "מפת חום - מוקדי דיווחים ברמת רחוב",
        "fr": "Carte de chaleur - points chauds des signalements par rue",
    },
    "heatmap.intro": {
        "he": "רחובות עם {min} דיווחים ומעלה בלבד, ברזולוציה מעוגנת\n"
        "של כ-111 מטר. ספירות בלבד - ללא נתונים גולמיים, לוחיות רישוי או שמות.\n"
        "הדף אינו טוען סקריפטים של צד שלישי מלבד מונה ביקורים אנונימי "
        "(Cloudflare Web Analytics) - ללא עוגיות וללא שמירת כתובות IP.",
        "fr": "Uniquement les rues avec {min} signalements ou plus, à une\n"
        "résolution arrondie d'environ 111 mètres. Décomptes uniquement, sans\n"
        "données brutes, plaques d'immatriculation ni noms. La page ne charge\n"
        "aucun script tiers hormis un compteur de visites anonyme (Cloudflare "
        "Web Analytics), sans cookies ni conservation des adresses IP.",
    },
    "heatmap.back": {
        "he": "חזרה לדף הראשי",
        "fr": "Retour à la page principale",
    },
    "heatmap.map_aria": {
        "he": "מפת מוקדים חמים ברמת רחוב",
        "fr": "Carte des points chauds par rue",
    },
    "heatmap.col.street": {
        "he": "רחוב",
        "fr": "Rue",
    },
    "heatmap.col.reports": {
        "he": "דיווחים",
        "fr": "Signalements",
    },
    "heatmap.cell_title": {
        "he": {"one": "{label}: {count} דיווחים", "other": "{label}: {count} דיווחים"},
        "fr": {
            "one": "{label} : {count} signalement",
            "other": "{label} : {count} signalements",
        },
    },
    "heatmap.note.below": {
        "he": {
            "one": "רחוב נוסף אחד מתחת לסף ולכן אינו מוצג.",
            "other": "{count} רחובות נוספים מתחת לסף ולכן אינם מוצגים.",
        },
        "fr": {
            "one": "{count} rue supplémentaire sous le seuil, donc non affichée.",
            "other": "{count} rues supplémentaires sous le seuil, donc non affichées.",
        },
    },
    "heatmap.note.unmapped": {
        "he": {
            "one": "רחוב נוסף אחד מעל הסף ללא מיפוי.",
            "other": "{count} רחובות נוספים מעל הסף ללא מיפוי.",
        },
        "fr": {
            "one": "{count} rue supplémentaire au-dessus du seuil, sans coordonnées.",
            "other": "{count} rues supplémentaires au-dessus du seuil, sans "
            "coordonnées.",
        },
    },
    "heatmap.note.outside": {
        "he": {
            "one": "מוקד נוסף אחד מחוץ לתצוגה הראשונית - מופיעים בטבלה, ואפשר "
            "לראותם על המפה בעזרת הזזה או הקטנה.",
            "other": "{count} מוקדים נוספים מחוץ לתצוגה הראשונית - מופיעים "
            "בטבלה, ואפשר לראותם על המפה בעזרת הזזה או הקטנה.",
        },
        "fr": {
            "one": "{count} point chaud supplémentaire hors de la vue initiale : "
            "il figure dans le tableau et apparaît sur la carte en la déplaçant "
            "ou en dézoomant.",
            "other": "{count} points chauds supplémentaires hors de la vue "
            "initiale : ils figurent dans le tableau et apparaissent sur la "
            "carte en la déplaçant ou en dézoomant.",
        },
    },
    "heatmap.legend_aria": {
        "he": "מקרא צבעים",
        "fr": "Légende des couleurs",
    },
    "heatmap.legend_label": {
        "he": "דיווחים:",
        "fr": "Signalements :",
    },
    "heatmap.zoom_in": {
        "he": "הגדלה",
        "fr": "Zoom avant",
    },
    "heatmap.zoom_out": {
        "he": "הקטנה",
        "fr": "Zoom arrière",
    },
    "heatmap.reset_title": {
        "he": "חזרה לתצוגה הראשונית",
        "fr": "Retour à la vue initiale",
    },
    "heatmap.reset": {
        "he": "איפוס",
        "fr": "Réinitialiser",
    },
    "heatmap.noscript": {
        "he": "ללא JavaScript מוצגת תצוגה סטטית של האזור החם; "
        "כל המוקדים מופיעים בטבלה.",
        "fr": "Sans JavaScript, une vue statique de la zone chaude est affichée ; "
        "tous les points chauds figurent dans le tableau.",
    },
    "heatmap.basemap_credit": {
        "he": "מפת הרקע:",
        "fr": "Fond de carte :",
    },
    "heatmap.footer": {
        "he": "נתונים מצטברים בלבד &middot; עודכן: {updated}",
        "fr": "Données agrégées uniquement &middot; Mis à jour : {updated}",
    },
    # Recovered snapshot landing page (recovered_page.py)
    "recovered.title": {
        "he": "פניות 106 - תמונת מצב משוחזרת",
        "fr": "Demandes 106 - état des lieux reconstitué",
    },
    "recovered.tag": {
        "he": "ירושלים · פניות 106",
        "fr": "Jérusalem · demandes 106",
    },
    "recovered.h1": {
        "he": "תמונת מצב משוחזרת",
        "fr": "État des lieux reconstitué",
    },
    "recovered.lede": {
        "he": "איחוד פרטי של ייצוא היסטורי, ספר ההגשות ואישורי פתיחה מהעירייה. באתר מוצגות ספירות בלבד.",
        "fr": "Fusion privée d'un export historique, du registre des dépôts et des accusés d'ouverture de la mairie. Seuls des décomptes sont publiés.",
    },
    "recovered.ledger_refreshed": {
        "he": "רענון אוטומטי אחרון של הספר: {when}",
        "fr": "Dernière actualisation automatique du registre : {when}",
    },
    "recovered.card_total": {
        "he": "מספרי פנייה שונים ששוחזרו",
        "fr": "numéros de demande distincts reconstitués",
    },
    "recovered.card_unresolved": {
        "he": "שורות בספר ללא שיוך מאומת למספר פנייה, לא נוספו לסך",
        "fr": "lignes du registre sans rattachement vérifié à un numéro de demande, non ajoutées au total",
    },
    "recovered.card_missing": {
        "he": "פניות ללא תאריך פתיחה מוסכם",
        "fr": "demandes sans date d'ouverture concordante",
    },
    "recovered.notice": {
        "he": "<strong>זה אינו מספר מוסמך של כל הפניות.</strong> זהו איחוד המקורות שנמצאו, לפי מספר פנייה מלא כולל סיומת. אין כאן ספירת פניות פתוחות כיום, שיעור סגירה או סטטוס חי מהעירייה; כל נתון סטטוס או סגירה הוא היסטורי בלבד.",
        "fr": "<strong>Ce n'est pas un chiffre officiel de toutes les demandes.</strong> C'est la fusion des sources retrouvées, par numéro de demande complet avec suffixe. Aucun décompte de demandes ouvertes aujourd'hui, taux de clôture ou statut en direct de la mairie ; tout statut ou clôture est historique uniquement.",
    },
    "recovered.sources_h": {
        "he": "מקורות ורענון",
        "fr": "Sources et actualisation",
    },
    "recovered.sources_p": {
        "he": "הנתונים מתרעננים ומתפרסמים אוטומטית. הספירה משלבת ייצוא היסטורי מוצפן (נכון ל-{snapshot}), את ספר ההגשות הנוכחי ואישורי פתיחה שנשמרו מהעירייה. פנייה שמופיעה ביותר ממקור אחד נספרת פעם אחת.",
        "fr": "Les données sont actualisées et publiées automatiquement. Le décompte combine un export historique chiffré (au {snapshot}), le registre actuel des dépôts et les accusés d'ouverture conservés de la mairie. Une demande présente dans plusieurs sources n'est comptée qu'une fois.",
    },
    "recovered.city_events": {
        "he": "אישורי פתיחה מהעירייה נשמרו עד {when}. זהו מועד שמירת האישורים, לא בדיקת סטטוס חיה בעירייה.",
        "fr": "Les accusés d'ouverture de la mairie sont conservés jusqu'au {when}. C'est la date de conservation, pas une vérification en direct du statut.",
    },
    "recovered.no_city_events": {
        "he": "טרם נשמרו אישורי פתיחה עדכניים מהעירייה מעבר לאיחוד ההיסטורי.",
        "fr": "Aucun accusé d'ouverture récent de la mairie n'est encore conservé au-delà de la fusion historique.",
    },
    "recovered.dates": {
        "he": "תאריכי פתיחה זמינים: <bdi>{first}</bdi> עד <bdi>{last}</bdi>. ב-{missing} פניות אין תאריך פתיחה מוסכם.",
        "fr": "Dates d'ouverture disponibles : <bdi>{first}</bdi> à <bdi>{last}</bdi>. {missing} demandes n'ont pas de date d'ouverture concordante.",
    },
    "recovered.no_dates": {
        "he": "אין עדיין תאריכי פתיחה זמינים.",
        "fr": "Aucune date d'ouverture disponible pour l'instant.",
    },
    "recovered.categories_h": {
        "he": "קטגוריות מובילות",
        "fr": "Principales catégories",
    },
    "recovered.streets_h": {
        "he": "רחובות מובילים",
        "fr": "Principales rues",
    },
    "recovered.group": {
        "he": "קבוצה",
        "fr": "Groupe",
    },
    "recovered.cases": {
        "he": "פניות",
        "fr": "Demandes",
    },
    "recovered.unknown": {
        "he": "אחר / לא ידוע",
        "fr": "Autre / inconnu",
    },
    "recovered.monthly_h": {
        "he": "לפי חודש פתיחה · {count} החודשים האחרונים עם נתונים",
        "fr": "Par mois d'ouverture · {count} derniers mois avec données",
    },
    "recovered.monthly_p": {
        "he": "תאריך פתיחה בלבד, ללא החלפה שקטה בתאריך צילום או הגשה. תאריכים סותרים נשארו לא ידועים.",
        "fr": "Date d'ouverture uniquement, sans substitution silencieuse par la date de photo ou de dépôt. Les dates contradictoires restent inconnues.",
    },
    "recovered.data_h": {
        "he": "הנתונים והארכיון",
        "fr": "Données et archive",
    },
    "recovered.link_summary": {
        "he": "סיכום משוחזר - JSON מצטבר בלבד",
        "fr": "Résumé reconstitué - JSON agrégé uniquement",
    },
    "recovered.link_freshness": {
        "he": "זמני רענון המקורות",
        "fr": "Dates d'actualisation des sources",
    },
    "recovered.link_archive": {
        "he": "ארכיון: תמונת ה-977 עד 2026-11-01",
        "fr": "Archive : l'état des 977 au 2026-11-01",
    },
    "recovered.link_archive_note": {
        "he": " - לא נתונים חיים ולא חלק נוסף לסך הנוכחי",
        "fr": " - pas des données en direct, ni une part en plus du total actuel",
    },
    "recovered.labels_note": {
        "he": "שמות קטגוריה ורחוב שלא נכללו ברשימת התוויות הציבוריות המותרות, או שיש להם ערכים סותרים, נכללים ב״אחר / לא ידוע״. קטגוריות כוללות גם שירות ומידע, לא רק מפגעי רכב.",
        "fr": "Les catégories et rues absentes de la liste publique autorisée, ou aux valeurs contradictoires, sont regroupées sous « Autre / inconnu ». Les catégories incluent aussi le service et l'information, pas seulement les infractions de stationnement.",
    },
    "recovered.footer": {
        "he": "אין כאן מזהי פנייה, לוחיות או נתונים גולמיים מהאיחוד המשוחזר. הקישורים לארכיון מציגים תמונת מצב ישנה.",
        "fr": "Aucun identifiant de demande, plaque ou donnée brute de la fusion reconstituée. Les liens d'archive montrent un état ancien.",
    },
}
