"""
online_features.py — Mealie recipe → serving input format

Imported by Mealie backend. Output must match
serving/sample_data/input_sample.json exactly.
"""

import re


def normalize_ingredient(raw_text):
    """Strip quantities, units, sizes, prep from raw ingredient string."""
    text = raw_text.lower()
    text = re.sub(r'^\d+[\d./\s]*', '', text)
    text = re.sub(r'^[¼½¾⅓⅔⅛⅜⅝⅞]\s*', '', text)
    text = re.sub(r'\b(cup|cups|tbsp|tablespoon|tablespoons|tsp|teaspoon|'
                  r'teaspoons|oz|ounce|ounces|lb|lbs|pound|pounds|g|gram|'
                  r'grams|kg|kilogram|ml|milliliter|l|liter|liters|pint|'
                  r'quart|gallon)s?\b', '', text)
    text = re.sub(r'\b(large|medium|small|extra|fresh|dried|whole|boneless|'
                  r'skinless|frozen|canned|packed|heaping|level)\b', '', text)
    text = re.sub(r'\b(chopped|diced|minced|sliced|grated|shredded|melted|'
                  r'softened|room temperature|peeled|seeded)\b', '', text)
    text = re.sub(r'\(.*?\)', '', text)
    text = re.sub(r'[,;].*', '', text)
    return ' '.join(text.split())


def build_serving_payload(mealie_recipe, missing_ingredient_raw):
    """Convert Mealie recipe dict → serving POST /predict JSON."""
    return {
        'recipe_id': str(mealie_recipe.get('id', '')),
        'recipe_title': mealie_recipe.get('name', ''),
        'ingredients': [
            {'raw': i.get('note', i.get('display', '')),
             'normalized': normalize_ingredient(i.get('note', i.get('display', '')))}
            for i in mealie_recipe.get('recipeIngredient', [])
            if i.get('note') or i.get('display')
        ],
        'instructions': [
            s['text'] for s in mealie_recipe.get('recipeInstructions', [])
            if s.get('text')
        ],
        'missing_ingredient': {
            'raw': missing_ingredient_raw,
            'normalized': normalize_ingredient(missing_ingredient_raw),
        },
    }
