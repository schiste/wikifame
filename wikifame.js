/* global mw */
/**
 * WikiFame — names the people who wrote the text you are reading.
 *
 * The script is wiki-agnostic: it reports its own wiki through wgDBname and lets the
 * API decide whether that wiki is served. An unsupported wiki answers 404, the fetch
 * rejects, and nothing is rendered — so this same file can ship on every Wikipedia
 * while the backend enables wikis one at a time.
 *
 * Wording and help links come from the reader's own User:<name>/wikifame-config.json
 * on the local wiki, alongside the script itself. While WikiFame is a personal script
 * the reader and the installer are the same person, so no interface-admin rights are
 * needed and each wiki gets its own copy. The page is optional: without it the
 * built-in defaults apply. Defaults per wiki are published in the repository.
 *
 * When this becomes a site-wide gadget the page moves to MediaWiki:Wikifame-config.json
 * and only CONFIG_PAGE_SUFFIX and configPage() change.
 */
( function () {
	'use strict';

	var ARTICLE_SUMMARY_ID = 'wikifame-summary';
	var HISTORY_INTRO_ID = 'wikifame-history-intro';
	var CACHE_VERSION = 'v2';
	var TOOLFORGE_API_BASE = 'https://wikifame.toolforge.org';
	var CONFIG_PAGE_SUFFIX = '/wikifame-config.json';
	var REQUEST_TIMEOUT_MS = 8000;
	var CLIENT_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000;
	var CONFIG_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000;
	var PENDING_RETRY_DELAYS_MS = [ 3000, 10000 ];

	var DEFAULT_CONFIG = {
		enabled: true,
		showHistoryIntro: true,
		editHelpPage: null,
		sandboxPage: null,
		messages: {}
	};

	/**
	 * Built-in wording. A wiki overrides any key through the "messages" object of its
	 * local configuration page, which is also how a new language gets translated
	 * without touching this script.
	 */
	var MESSAGES = {
		en: {
			'wikifame-summary-prefix': 'Article written by ',
			'wikifame-people': '{{PLURAL:$1|$1 person|$1 people}}',
			'wikifame-others': '{{PLURAL:$1|$1 other person|$1 other people}}',
			'wikifame-at-least': 'at least $1',
			'wikifame-user-title': 'View the user page of $1',
			'wikifame-share': '$1 of the currently visible tokens',
			'wikifame-history-title': 'View the full page history',
			'wikifame-tooltip': 'Main authors of the text according to WikiWho.',
			'wikifame-computed': 'Data computed on $1.',
			'wikifame-history-intro': 'Each line is one version of the article, showing who changed it.',
			'wikifame-history-help': 'To get started, read $1 or practise in $2.',
			'wikifame-history-help-label': 'the editing help',
			'wikifame-history-sandbox-label': 'the sandbox',
			'wikifame-history-edit': 'You can also $1.',
			'wikifame-history-edit-label': 'edit this article directly'
		},
		fr: {
			'wikifame-summary-prefix': 'Article rédigé par ',
			'wikifame-people': '{{PLURAL:$1|$1 personne|$1 personnes}}',
			'wikifame-others': '{{PLURAL:$1|$1 autre personne|$1 autres personnes}}',
			'wikifame-at-least': 'au moins $1',
			'wikifame-user-title': 'Voir la page utilisateur de $1',
			'wikifame-share': '$1 des tokens actuellement visibles',
			'wikifame-history-title': 'Voir l’historique complet de l’article',
			'wikifame-tooltip': 'Principaux contributeurs du texte selon WikiWho.',
			'wikifame-computed': 'Données calculées le $1.',
			'wikifame-history-intro': 'Chaque ligne correspond à une version de l’article et indique qui l’a modifiée.',
			'wikifame-history-help': 'Pour commencer, consultez $1 ou entraînez-vous dans $2.',
			'wikifame-history-help-label': 'l’aide à la modification',
			'wikifame-history-sandbox-label': 'le bac à sable',
			'wikifame-history-edit': 'Vous pouvez aussi $1.',
			'wikifame-history-edit-label': 'modifier directement cet article'
		}
	};

	var config = mw.config.get( [
		'wgAction',
		'wgArticleId',
		'wgCurRevisionId',
		'wgDBname',
		'wgDiffNewId',
		'wgDiffOldId',
		'wgNamespaceNumber',
		'wgPageName',
		'wgRevisionId',
		'wgUserLanguage',
		'wgUserName'
	] );

	var numberFormatter;
	var percentageFormatter;
	var dateFormatter;
	var listFormatter;

	if ( config.wgNamespaceNumber !== 0 || !config.wgArticleId || !config.wgDBname ) {
		return;
	}

	mw.loader.using( [ 'mediawiki.util', 'mediawiki.Title', 'mediawiki.jqueryMsg' ] )
		.then( loadWikiConfig )
		.then( function ( wikiConfig ) {
			if ( wikiConfig.enabled === false ) {
				return;
			}

			installMessages( wikiConfig );
			installFormatters();

			if ( config.wgAction === 'history' ) {
				if ( wikiConfig.showHistoryIntro !== false ) {
					addHistoryIntroduction( wikiConfig );
				}
				return;
			}

			if (
				config.wgAction === 'view' &&
				!config.wgDiffOldId &&
				!config.wgDiffNewId &&
				config.wgRevisionId === config.wgCurRevisionId
			) {
				return addArticleSummary();
			}
		} )
		.catch( function ( error ) {
			mw.log.warn( 'WikiFame: initialisation failed', error );
		} );

	/* -------------------------------------------------------------- configuration */

	/**
	 * Title of the configuration page for this reader on this wiki, or null when there
	 * is nobody to attribute it to. Namespace 2 resolves to the wiki's own localised
	 * user-namespace name, so this works unchanged on every language edition.
	 */
	function configPage() {
		if ( !config.wgUserName ) {
			return null;
		}
		return new mw.Title( config.wgUserName + CONFIG_PAGE_SUFFIX, 2 ).getPrefixedDb();
	}

	/**
	 * Read the configuration page. A missing page is the normal case for someone who has
	 * not customised anything, so a 404 resolves to the defaults rather than rejecting.
	 */
	async function loadWikiConfig() {
		var page = configPage();
		var cacheKey;
		var cached;
		var url;
		var response;
		var parsed;

		if ( !page ) {
			return Object.assign( {}, DEFAULT_CONFIG );
		}

		// The page is per user as well as per wiki, so both belong in the key: a shared
		// browser must not serve one account's configuration to the next.
		cacheKey = 'wikifame:config:' + CACHE_VERSION + ':' + config.wgDBname + ':' + page;
		cached = readCache( cacheKey, CONFIG_CACHE_MAX_AGE_MS );

		if ( cached ) {
			return Object.assign( {}, DEFAULT_CONFIG, cached );
		}

		url = mw.util.wikiScript( 'index' ) +
			'?title=' + encodeURIComponent( page ) +
			'&action=raw&ctype=application/json';

		try {
			response = await fetch( url, {
				headers: { Accept: 'application/json' },
				credentials: 'omit'
			} );
			parsed = response.ok ? await response.json() : {};
		} catch ( error ) {
			parsed = {};
		}

		if ( !parsed || typeof parsed !== 'object' || Array.isArray( parsed ) ) {
			parsed = {};
		}

		writeCache( cacheKey, parsed );
		return Object.assign( {}, DEFAULT_CONFIG, parsed );
	}

	function installMessages( wikiConfig ) {
		var language = config.wgUserLanguage || 'en';
		var base = language.split( '-' )[ 0 ];
		var table = Object.assign(
			{},
			MESSAGES.en,
			MESSAGES[ base ] || {},
			MESSAGES[ language ] || {}
		);

		if ( wikiConfig.messages && typeof wikiConfig.messages === 'object' ) {
			Object.keys( wikiConfig.messages ).forEach( function ( key ) {
				if ( typeof wikiConfig.messages[ key ] === 'string' ) {
					table[ key ] = wikiConfig.messages[ key ];
				}
			} );
		}

		mw.messages.set( table );
	}

	/**
	 * Not every MediaWiki language code is a valid BCP 47 tag, so each formatter falls
	 * back through the base language to English rather than throwing.
	 */
	function safeFormatter( build ) {
		var language = config.wgUserLanguage || 'en';
		var candidates = [ language, language.split( '-' )[ 0 ], 'en' ];
		var index;

		for ( index = 0; index < candidates.length; index++ ) {
			try {
				return build( candidates[ index ] );
			} catch ( error ) {
				continue;
			}
		}
		return null;
	}

	function installFormatters() {
		numberFormatter = safeFormatter( function ( locale ) {
			return new Intl.NumberFormat( locale );
		} );
		percentageFormatter = safeFormatter( function ( locale ) {
			return new Intl.NumberFormat( locale, {
				maximumFractionDigits: 1,
				style: 'percent'
			} );
		} );
		dateFormatter = safeFormatter( function ( locale ) {
			return new Intl.DateTimeFormat( locale, {
				day: 'numeric',
				month: 'long',
				year: 'numeric'
			} );
		} );
		listFormatter = typeof Intl !== 'undefined' && Intl.ListFormat ?
			safeFormatter( function ( locale ) {
				return new Intl.ListFormat( locale, { style: 'long', type: 'conjunction' } );
			} ) :
			null;
	}

	function formatNumber( value ) {
		return numberFormatter ? numberFormatter.format( value ) : String( value );
	}

	/* ------------------------------------------------------------------ rendering */

	function insertBelowSubtitle( element ) {
		var siteSub = document.getElementById( 'siteSub' );
		var vectorSlot = document.querySelector( '.vector-body-before-content' );
		var bodyContent = document.getElementById( 'bodyContent' );

		if ( siteSub ) {
			siteSub.after( element );
			return true;
		}

		if ( vectorSlot ) {
			vectorSlot.prepend( element );
			return true;
		}

		if ( bodyContent ) {
			bodyContent.prepend( element );
			return true;
		}

		return false;
	}

	/**
	 * Replace $1, $2… in a message with DOM nodes, so a sentence can contain links
	 * without ever building HTML from a string.
	 */
	function appendMessageWithNodes( parent, messageKey, nodes ) {
		var text = mw.message( messageKey ).text();
		var pattern = /\$(\d+)/g;
		var lastIndex = 0;
		var match;

		while ( ( match = pattern.exec( text ) ) !== null ) {
			if ( match.index > lastIndex ) {
				parent.append( document.createTextNode( text.slice( lastIndex, match.index ) ) );
			}
			parent.append( nodes[ Number( match[ 1 ] ) - 1 ] || document.createTextNode( '' ) );
			lastIndex = match.index + match[ 0 ].length;
		}

		if ( lastIndex < text.length ) {
			parent.append( document.createTextNode( text.slice( lastIndex ) ) );
		}
	}

	function addHistoryIntroduction( wikiConfig ) {
		var box;
		var line;

		if ( document.getElementById( HISTORY_INTRO_ID ) ) {
			return;
		}

		box = document.createElement( 'div' );
		box.id = HISTORY_INTRO_ID;
		box.className = 'wikifame wikifame--history';
		box.setAttribute( 'role', 'note' );

		line = document.createElement( 'p' );
		line.textContent = mw.message( 'wikifame-history-intro' ).text();
		box.append( line );

		// The help and sandbox pages have no cross-wiki names, so this sentence only
		// appears where the local configuration page supplies both titles.
		if ( wikiConfig.editHelpPage && wikiConfig.sandboxPage ) {
			line = document.createElement( 'p' );
			appendMessageWithNodes( line, 'wikifame-history-help', [
				createWikiLink(
					wikiConfig.editHelpPage,
					mw.message( 'wikifame-history-help-label' ).text()
				),
				createWikiLink(
					wikiConfig.sandboxPage,
					mw.message( 'wikifame-history-sandbox-label' ).text()
				)
			] );
			box.append( line );
		}

		line = document.createElement( 'p' );
		appendMessageWithNodes( line, 'wikifame-history-edit', [ createEditLink() ] );
		box.append( line );

		insertBelowSubtitle( box );
	}

	async function addArticleSummary() {
		var cacheKey = getCacheKey();
		var cached = readCache( cacheKey, CLIENT_CACHE_MAX_AGE_MS );
		var data;
		var summary;

		if ( document.getElementById( ARTICLE_SUMMARY_ID ) ) {
			return;
		}

		try {
			data = cached || await loadContributionData();
			if ( !data ) {
				return;
			}
			if ( !cached ) {
				writeCache( cacheKey, data );
			}
			summary = buildArticleSummary( data );
			if ( summary ) {
				insertBelowSubtitle( summary );
			}
		} catch ( error ) {
			mw.log.warn( 'WikiFame: attribution unavailable', error );
		}
	}

	async function loadContributionData() {
		var url = TOOLFORGE_API_BASE + '/v2/' +
			encodeURIComponent( config.wgDBname ) + '/pages/' +
			encodeURIComponent( config.wgArticleId ) +
			'?revision_id=' + encodeURIComponent( config.wgCurRevisionId );
		var attempt;
		var data;

		for ( attempt = 0; attempt <= PENDING_RETRY_DELAYS_MS.length; attempt++ ) {
			data = await fetchJson( url, {
				credentials: 'omit',
				referrerPolicy: 'no-referrer'
			} );

			if ( data.status === 'ready' ) {
				if (
					typeof data.distinct_contributors !== 'number' ||
					!Array.isArray( data.contributors )
				) {
					throw new Error( 'Invalid attribution response.' );
				}

				return normalizeContributionData( data );
			}

			if ( data.status !== 'pending' ) {
				throw new Error( 'Unknown attribution state.' );
			}

			if ( attempt < PENDING_RETRY_DELAYS_MS.length ) {
				await wait( PENDING_RETRY_DELAYS_MS[ attempt ] );
			}
		}

		return null;
	}

	function normalizeContributionData( data ) {
		return {
			computedAt: data.computed_at,
			humanCount: data.distinct_contributors,
			limited: Boolean( data.count_limited ),
			topEditors: data.contributors.slice( 0, 3 ).map( function ( editor ) {
				return {
					share: Number( editor.share ),
					username: editor.username
				};
			} )
		};
	}

	function wait( delayMs ) {
		return new Promise( function ( resolve ) {
			window.setTimeout( resolve, delayMs );
		} );
	}

	async function fetchJson( url, options ) {
		var controller = new AbortController();
		var timeout = window.setTimeout( function () {
			controller.abort();
		}, REQUEST_TIMEOUT_MS );
		var response;

		try {
			response = await fetch( url, Object.assign( {
				headers: {
					Accept: 'application/json'
				},
				signal: controller.signal
			}, options ) );

			if ( !response.ok ) {
				throw new Error( 'HTTP error ' + response.status );
			}

			return await response.json();
		} finally {
			window.clearTimeout( timeout );
		}
	}

	function buildArticleSummary( data ) {
		var box = document.createElement( 'div' );
		var topEditors = data.topEditors;
		var otherCount = Math.max( 0, data.humanCount - topEditors.length );
		var computedDate = new Date( data.computedAt );
		var nodes;

		if ( data.humanCount < 1 ) {
			return null;
		}

		box.id = ARTICLE_SUMMARY_ID;
		box.className = 'wikifame wikifame--article';
		box.setAttribute( 'role', 'note' );
		box.title = mw.message( 'wikifame-tooltip' ).text();
		if ( !Number.isNaN( computedDate.getTime() ) && dateFormatter ) {
			box.title += ' ' + mw.message(
				'wikifame-computed',
				dateFormatter.format( computedDate )
			).text();
		}

		box.append( document.createTextNode( mw.message( 'wikifame-summary-prefix' ).text() ) );

		if ( topEditors.length ) {
			nodes = topEditors.map( createEditorLink );
			if ( otherCount > 0 ) {
				nodes.push( createHistoryCountLink( otherCount, data.limited, true ) );
			}
		} else {
			nodes = [ createHistoryCountLink( data.humanCount, data.limited, false ) ];
		}

		appendList( box, nodes );
		box.append( document.createTextNode( '.' ) );
		return box;
	}

	/**
	 * Join the links with the conjunction of the reader's language. Intl.ListFormat
	 * knows that English wants "A, B and C" while other languages differ, so the
	 * separator is never hard-coded. Placeholders carry the node index through
	 * formatToParts, which keeps real DOM elements in the sentence.
	 */
	function appendList( parent, nodes ) {
		var placeholders;
		var parts;

		if ( nodes.length === 1 ) {
			parent.append( nodes[ 0 ] );
			return;
		}

		if ( !listFormatter ) {
			nodes.forEach( function ( node, index ) {
				if ( index > 0 ) {
					parent.append( document.createTextNode( ', ' ) );
				}
				parent.append( node );
			} );
			return;
		}

		placeholders = nodes.map( function ( _node, index ) {
			return ' ' + index;
		} );
		parts = listFormatter.formatToParts( placeholders );

		parts.forEach( function ( part ) {
			if ( part.type === 'element' ) {
				parent.append( nodes[ Number( part.value.slice( 1 ) ) ] );
			} else {
				parent.append( document.createTextNode( part.value ) );
			}
		} );
	}

	function createEditorLink( editor ) {
		var link = document.createElement( 'a' );
		var name = editor.username.replace( /_/g, ' ' );
		var title = new mw.Title( editor.username, 2 );

		link.href = title.getUrl();
		link.textContent = name;
		link.title = mw.message( 'wikifame-user-title', name ).text();
		if ( Number.isFinite( editor.share ) && percentageFormatter ) {
			link.title += ' — ' + mw.message(
				'wikifame-share',
				percentageFormatter.format( editor.share )
			).text();
		}
		return link;
	}

	function createHistoryCountLink( count, limited, isRemainder ) {
		var link = document.createElement( 'a' );
		var label = mw.message(
			isRemainder ? 'wikifame-others' : 'wikifame-people',
			formatNumber( count )
		).text();

		if ( limited ) {
			label = mw.message( 'wikifame-at-least', label ).text();
		}

		link.href = mw.util.getUrl( config.wgPageName, { action: 'history' } );
		link.textContent = label;
		link.title = mw.message( 'wikifame-history-title' ).text();
		return link;
	}

	function createWikiLink( title, label ) {
		var link = document.createElement( 'a' );
		link.href = mw.util.getUrl( title );
		link.textContent = label;
		return link;
	}

	function createEditLink() {
		var link = document.createElement( 'a' );
		link.href = mw.util.getUrl( config.wgPageName, { veaction: 'edit' } );
		link.textContent = mw.message( 'wikifame-history-edit-label' ).text();
		return link;
	}

	/* -------------------------------------------------------------------- caching */

	function getCacheKey() {
		return 'wikifame:' + CACHE_VERSION + ':' +
			config.wgDBname + ':' +
			config.wgArticleId;
	}

	function readCache( key, maxAgeMs ) {
		var raw;
		var cached;

		try {
			raw = window.sessionStorage.getItem( key );
			cached = raw ? JSON.parse( raw ) : null;
			if (
				!cached ||
				typeof cached.storedAt !== 'number' ||
				Date.now() - cached.storedAt > maxAgeMs
			) {
				window.sessionStorage.removeItem( key );
				return null;
			}
			return cached.data || null;
		} catch ( error ) {
			return null;
		}
	}

	function writeCache( key, data ) {
		try {
			window.sessionStorage.setItem( key, JSON.stringify( {
				data: data,
				storedAt: Date.now()
			} ) );
		} catch ( error ) {
			// The gadget stays functional when storage is disabled or full.
		}
	}
}() );
