import { useEffect, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { useLocation } from 'react-router-dom';
import { searchClient, getIndexNames, userToken, chatAgentId } from '../utilities/algolia';
import { useEvent } from '../context/EventContext';
import { scrollToSearchBox } from '../utilities/dom';
import {
  Configure,
  Hits,
  InstantSearch,
  Pagination,
  PoweredBy,
  SearchBox,
  SortBy,
  useClearRefinements,
  useHits,
  useInstantSearch,
  useSearchBox,
  useSortBy,
} from 'react-instantsearch';
import aa from 'search-insights';
import Header from './Header';
import Hit from './Hit';
import FilterDropdown from './FilterDropdown';
import FilterToggle from './FilterToggle';
import Carousel from './Carousel';
import ClaimedCarousel from './ClaimedCarousel';
import ChatAgent from './ChatAgent';
import AiModeButton from './AiModeButton';

// Set user token for insights
aa('setUserToken', userToken);

// Sits inside InstantSearch — sets the query and scrolls to results on mount
function ScanQuerySetter({ query }) {
  const { refine } = useSearchBox();
  useEffect(() => {
    if (!query) return;
    refine(query);
    scrollToSearchBox();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

ScanQuerySetter.propTypes = {
  query: PropTypes.string.isRequired,
};

function triggerAiMode() {
  document.querySelector('.ais-AiModeButton')?.click();
}

// Sits inside InstantSearch — intercepts Enter to open AI chat when query is non-empty
function SearchBoxWithAISubmit() {
  const { query } = useSearchBox();
  return (
    <SearchBox
      placeholder="Search for cards"
      className="searchbox"
      aiMode
      onSubmit={() => {
        if (query.trim()) triggerAiMode();
      }}
    />
  );
}

function ClearButton({ defaultSort, sortItems, chatFilters, clearChatFilters }) {
  const { refine: clearRefinements, canRefine } = useClearRefinements();
  const { currentRefinement: currentSort, refine: setSort } = useSortBy({ items: sortItems });
  const sortChanged = currentSort !== defaultSort;

  if (!canRefine && !sortChanged && !chatFilters) return null;

  function handleClear() {
    clearRefinements();
    if (sortChanged) setSort(defaultSort);
    if (chatFilters) clearChatFilters();
  }

  return (
    <button className="filter-clear-btn" onClick={handleClear} aria-label="Clear filters">
      <span className="label-full">✕ Clear</span>
      <span className="label-short">✕</span>
    </button>
  );
}

ClearButton.propTypes = {
  defaultSort: PropTypes.string.isRequired,
  sortItems: PropTypes.arrayOf(
    PropTypes.shape({ label: PropTypes.string, value: PropTypes.string })
  ).isRequired,
  chatFilters: PropTypes.string,
  clearChatFilters: PropTypes.func,
};

function HitsWithNoResults() {
  const { results } = useHits();
  const hasResults = results && results.hits.length > 0;

  if (!hasResults) {
    return (
      <div className="no-results">
        <h2 className="no-results-title">No cards found</h2>
        <p className="no-results-description">
          Try asking the AI — it can help with card availability, prices, and recommendations.
        </p>
        <AiModeButton onClick={triggerAiMode} />
      </div>
    );
  }

  return (
    <>
      <Hits hitComponent={Hit} />
      <div className="pagination">
        <Pagination />
      </div>
    </>
  );
}

// Reads the last completed search tool call from chat messages and extracts
// facet_* fields (e.g. facet_pokemon_types, facet_is_full_art) into a raw
// Algolia filters string. The library's applyFilters only handles query and
// facet_filters, not these custom MCP tool fields.
function ChatViewAllHandler({ onFiltersChange }) {
  const { indexRenderState } = useInstantSearch();
  const stateRef = useRef(indexRenderState);

  useEffect(() => {
    stateRef.current = indexRenderState;
  }, [indexRenderState]);

  useEffect(() => {
    const handleViewAll = (e) => {
      if (!e.target.closest('.ais-ChatToolSearchIndexCarouselHeaderViewAll')) return;

      scrollToSearchBox();

      const messages = stateRef.current?.chat?.messages ?? [];
      let toolInput = null;
      outer: for (let i = messages.length - 1; i >= 0; i--) {
        const msg = messages[i];
        if (msg.role !== 'assistant') continue;
        for (const part of (msg.parts ?? [])) {
          if (
            part.type?.startsWith('tool-algolia_search_index') &&
            part.state === 'output-available' &&
            part.input
          ) {
            toolInput = part.input;
            break outer;
          }
        }
      }

      const filterParts = [];
      if (toolInput) {
        for (const [key, value] of Object.entries(toolInput)) {
          if (!key.startsWith('facet_')) continue;
          const attribute = key.slice(6);
          if (Array.isArray(value)) {
            value.forEach((v) => filterParts.push(`${attribute}:"${v}"`));
          } else if (typeof value === 'boolean') {
            filterParts.push(`${attribute}:${value}`);
          } else if (value != null) {
            filterParts.push(`${attribute}:"${value}"`);
          }
        }
      }

      onFiltersChange(filterParts.join(' AND '));
    };

    document.addEventListener('click', handleViewAll);
    return () => document.removeEventListener('click', handleViewAll);
  }, [onFiltersChange]);

  return null;
}

ChatViewAllHandler.propTypes = {
  onFiltersChange: PropTypes.func.isRequired,
};

export default function Search() {
  const { eventConfig, loading, error } = useEvent();
  const location = useLocation();
  // Capture in useState — location.state is wiped by InstantSearch's routing on mount
  const [searchQuery] = useState(location.state?.searchQuery ?? '');
  const [shouldScrollToSearch] = useState(location.state?.scrollToSearch ?? false);
  const [chatFilters, setChatFilters] = useState('');

  useEffect(() => {
    if (!shouldScrollToSearch) return;
    scrollToSearchBox();
  }, [shouldScrollToSearch]);

  // Override mobile browser's default scroll-to-center behavior on input focus
  useEffect(() => {
    const handleFocus = (e) => {
      if (e.target.matches('.ais-SearchBox-input')) {
        // Small delay to override browser's default scroll
        setTimeout(() => {
          e.target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 100);
      }
    };

    document.addEventListener('focus', handleFocus, true);
    return () => document.removeEventListener('focus', handleFocus, true);
  }, []);

  if (loading) return <div className="event-loading">Loading event…</div>;
  if (error || !eventConfig) return <div className="event-error">Event not found.</div>;

  const { primary, priceAsc, priceDesc } = getIndexNames(eventConfig.event_id);
  const agentId = eventConfig.agent_id || chatAgentId;
  const sortItems = [
    { label: 'Relevance', value: primary },
    { label: 'Sort Price ↑', value: priceAsc },
    { label: 'Sort Price ↓', value: priceDesc },
  ];

  return (
    <div>
      <Header />
      <div className="container">
        <InstantSearch
          searchClient={searchClient}
          indexName={priceDesc}
          routing={true}
          insights={{
            insightsClient: aa,
            insightsInitParams: {
              useCookie: true
            }
          }}
        >
          <Configure
            hitsPerPage={12}
            clickAnalytics={true}
            filters={chatFilters || undefined}
          />
          {searchQuery && <ScanQuerySetter query={searchQuery} />}

          {/* Powered by Algolia */}
          <div className="powered-by-container">
            <span className="event-name">
              {eventConfig.name}{eventConfig.booth ? ` (Booth ${eventConfig.booth})` : ''}
            </span>
            <PoweredBy />
          </div>

          {/* Top 10 carousel always shown */}
          <Carousel title="⭐ Top 10 Chase Cards" filters="is_top_10_chase_card:true" hitsPerPage={10} />

          {/* Additional carousels defined per-event */}
          {eventConfig.landing_sections?.map(({ title, filter }) => (
            <Carousel key={title} title={title} filters={filter} hitsPerPage={10} />
          ))}

          {/* Recently Claimed Carousel */}
          <ClaimedCarousel />

          <div className="search-header">
            <div className="search-controls-row">
              <SearchBoxWithAISubmit />
              <FilterDropdown attribute="set_name" placeholder="All Sets" />
              <FilterToggle attribute="is_chase_card" label="Chase Cards" shortLabel="Chase" />
              <SortBy items={sortItems} />
              <ClearButton defaultSort={priceDesc} sortItems={sortItems} chatFilters={chatFilters} clearChatFilters={() => setChatFilters('')} />
            </div>
          </div>

          {/* AI Chat Agent */}
          <ChatAgent agentId={agentId} />
          <ChatViewAllHandler onFiltersChange={setChatFilters} />

          <div className="search-panel">
            <div className="search-panel__results">
              <HitsWithNoResults />
            </div>
          </div>
        </InstantSearch>
      </div>
    </div>
  );
}
