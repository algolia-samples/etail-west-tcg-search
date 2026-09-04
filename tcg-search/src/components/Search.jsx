import { useEffect, useState } from 'react';
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

function ClearButton({ defaultSort, sortItems }) {
  const { refine: clearRefinements, canRefine } = useClearRefinements();
  const { currentRefinement: currentSort, refine: setSort } = useSortBy({ items: sortItems });
  const sortChanged = currentSort !== defaultSort;

  if (!canRefine && !sortChanged) return null;

  function handleClear() {
    clearRefinements();
    if (sortChanged) setSort(defaultSort);
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

// The chat carousel's "View all" button applies the agent's filters itself:
// react-instantsearch reads them from the search tool's `queries[0]`, and
// prefers the server's resolved search params where those are present. All
// this has to add is bringing the search box into view.
function ChatViewAllScroll() {
  useEffect(() => {
    const handleViewAll = (e) => {
      if (!e.target.closest('.ais-ChatToolSearchIndexCarouselHeaderViewAll')) return;
      scrollToSearchBox();
    };

    document.addEventListener('click', handleViewAll);
    return () => document.removeEventListener('click', handleViewAll);
  }, []);

  return null;
}

export default function Search() {
  const { eventConfig, loading, error } = useEvent();
  const location = useLocation();
  // Capture in useState — location.state is wiped by InstantSearch's routing on mount
  const [searchQuery] = useState(location.state?.searchQuery ?? '');
  const [shouldScrollToSearch] = useState(location.state?.scrollToSearch ?? false);

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
              <ClearButton defaultSort={priceDesc} sortItems={sortItems} />
            </div>
          </div>

          {/* AI Chat Agent */}
          <ChatAgent agentId={agentId} />
          <ChatViewAllScroll />

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
