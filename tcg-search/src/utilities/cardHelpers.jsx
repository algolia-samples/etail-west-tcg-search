// Helper to get card type badge color
export function getCardTypeColor(cardType) {
  const colors = {
    'Full Art': '#e74c3c',
    'Alternative Full Art': '#ff6b6b',
    'Gold': '#f39c12',
    'Secret Art': '#9b59b6',
    'Holo': '#3498db',
    'Reverse Holo': '#1abc9c',
    'Double Rare': '#2980b9',
    'Ultra Rare': '#8e44ad',
    'Illustration Rare': '#e67e22',
    'Special Illustration Rare': '#c0392b',
    // Base rarities (RetailClub AI Festival onward): a desaturated ramp so they read
    // as lower-tier and don't compete with the chase-card colors above.
    'Promo': '#d35400',
    'Rare': '#5d6d7e',
    'Uncommon': '#7f8c8d',
    'Common': '#95a5a6',
  };
  return colors[cardType] || '#3B4CCA';
}

// Helper to format set name with line break after colon
export function formatSetName(setName) {
  if (!setName) return null;
  const parts = setName.split(':');
  if (parts.length > 1) {
    return (
      <>
        {parts[0]}:<br />{parts.slice(1).join(':').trim()}
      </>
    );
  }
  return setName;
}

// Helper to extract rotation from transform matrix
export function getRotationFromMatrix(element) {
  try {
    const computedStyle = window.getComputedStyle(element);
    const matrix = computedStyle.transform;

    if (!matrix || matrix === 'none') {
      return 0;
    }

    const values = matrix.split('(')[1]?.split(')')[0]?.split(',');
    if (!values || values.length < 2) {
      return 0;
    }

    const a = parseFloat(values[0]);
    const b = parseFloat(values[1]);

    if (isNaN(a) || isNaN(b)) {
      return 0;
    }

    return Math.round(Math.atan2(b, a) * (180 / Math.PI));
  } catch (error) {
    console.warn('Failed to extract rotation from matrix:', error);
    return 0;
  }
}

// Shared placeholder card style (hoisted to avoid recreating on every render)
export const PLACEHOLDER_CARD_STYLE = {
  width: '245px',
  height: '342px',
  background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  color: 'white',
  fontSize: '1.2rem',
  fontWeight: 'bold',
  textAlign: 'center',
  padding: '1rem',
  borderRadius: '8px',
};

// Shared cursor pointer style
export const CURSOR_POINTER_STYLE = { cursor: 'pointer' };
