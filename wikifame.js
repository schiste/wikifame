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
 *
 * Two extension points exist so that nobody has to fork this file:
 *
 *   historyIntroPage  a wikitext page whose parsed HTML replaces the built-in history
 *                     introduction. Images, galleries, Commons video and templates all
 *                     work, because MediaWiki does the parsing and the sanitising.
 *   count slots       an element of class wikifame-count or wikifame-number in that page
 *                     receives this article's contributor count, which the page itself
 *                     cannot hold: it is parsed once and cached for every article.
 *   mw.hook           'wikifame.history' and 'wikifame.summary' fire with the rendered
 *                     element, so arbitrary JavaScript belongs in the reader's own
 *                     common.js rather than in a configuration page.
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
	var CONTENT_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000;
	var PENDING_RETRY_DELAYS_MS = [ 3000, 10000 ];

	// A warm response lands well inside COUNT_ROLL_START_MS, so the usual reader never
	// sees a placeholder at all: showing one immediately would invent a wait that is not
	// there. Rolling stops at COUNT_ROLL_SETTLE_MS because the retry chain can run for
	// thirteen seconds and nobody watches digits spin that long — the box settles on
	// wording that is true whatever the answer, and the real sentence replaces it later.
	var COUNT_ROLL_START_MS = 200;
	var COUNT_ROLL_SETTLE_MS = 2500;
	var COUNT_ROLL_FRAME_MS = 80;
	var COUNT_ROLL_DIGITS = 4;

	var DEFAULT_CONFIG = {
		enabled: true,
		showHistoryIntro: true,
		editHelpPage: null,
		sandboxPage: null,
		historyIntroPage: null,
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
			'wikifame-many-people': 'many people',
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
			'wikifame-many-people': 'de nombreuses personnes',
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
					return addHistoryIntroduction( wikiConfig );
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

	/* ------------------------------------------------------------- custom content */

	/**
	 * Titles to try for a reader's own rich content, most specific first.
	 *
	 * A wikitext page is written in one language, so translations live on language
	 * subpages: /fr-ca, then /fr, then the base title. That keeps one reviewable page
	 * per language instead of one page in whichever language its author happened to
	 * speak, which is the problem the flat "messages" object has.
	 */
	function contentCandidates( base ) {
		var language = config.wgUserLanguage || 'en';
		var candidates = [ base + '/' + language ];

		if ( language.indexOf( '-' ) !== -1 ) {
			candidates.push( base + '/' + language.split( '-' )[ 0 ] );
		}
		candidates.push( base );
		return candidates;
	}

	/**
	 * Parsed HTML for one title, or null when it does not exist.
	 *
	 * Anonymous so the response stays CDN-cacheable, and asks for a short server-side
	 * cache: an introduction changes rarely but is read on every history view.
	 */
	async function fetchParsedPage( title ) {
		var url = mw.util.wikiScript( 'api' ) +
			'?action=parse&format=json&formatversion=2&prop=text' +
			'&redirects=1&disablelimitreport=1&disableeditsection=1' +
			'&smaxage=300&maxage=300' +
			'&page=' + encodeURIComponent( title );
		var response;
		var data;

		try {
			response = await fetch( url, {
				headers: { Accept: 'application/json' },
				credentials: 'omit'
			} );
			if ( !response.ok ) {
				return null;
			}
			data = await response.json();
		} catch ( error ) {
			return null;
		}

		// A missing page reports an error code rather than an HTTP status, and is the
		// normal case for a reader who has not written one.
		if ( !data || !data.parse || typeof data.parse.text !== 'string' ) {
			return null;
		}
		return data.parse.text;
	}

	/**
	 * Rich introduction for this reader, already parsed by MediaWiki, or null.
	 *
	 * Wikitext buys images, galleries, Commons video and templates for nothing, and the
	 * parser sanitises it, so this script never has to build markup from a string or
	 * trust the page to be well-behaved.
	 */
	async function loadCustomContent( title ) {
		var cacheKey;
		var cached;
		var candidates;
		var index;
		var html = null;

		if ( typeof title !== 'string' || !title ) {
			return null;
		}

		cacheKey = 'wikifame:content:' + CACHE_VERSION + ':' + config.wgDBname + ':' +
			title + ':' + ( config.wgUserLanguage || 'en' );
		cached = readCache( cacheKey, CONTENT_CACHE_MAX_AGE_MS );

		if ( cached ) {
			return cached.html;
		}

		candidates = contentCandidates( title );

		for ( index = 0; index < candidates.length && !html; index++ ) {
			html = await fetchParsedPage( candidates[ index ] );
		}

		// The absence of a page is cached too. Otherwise a configured but unwritten
		// title costs three failed lookups on every single history view.
		writeCache( cacheKey, { html: html } );
		return html;
	}

	/**
	 * Turn parser output into nodes.
	 *
	 * DOMParser builds an inert document, so nothing loads or runs while the fragment is
	 * assembled. Wikitext cannot produce a script element in the first place; removing
	 * any that appear keeps that true of this function on its own, without having to
	 * reason about the whole parser pipeline to review it.
	 */
	function renderCustomContent( html ) {
		var parsed = new DOMParser().parseFromString( html, 'text/html' );
		var container = document.createElement( 'div' );

		parsed.querySelectorAll( 'script' ).forEach( function ( node ) {
			node.remove();
		} );

		// A note box is not worth blocking a page render for: media loads when reached,
		// and video never starts on its own in something the reader did not ask to play.
		parsed.querySelectorAll( 'img' ).forEach( function ( image ) {
			image.setAttribute( 'loading', 'lazy' );
		} );
		parsed.querySelectorAll( 'video' ).forEach( function ( video ) {
			video.removeAttribute( 'autoplay' );
			video.setAttribute( 'preload', 'none' );
		} );

		if ( parsed.querySelector( 'video, .mw-tmh-player' ) ) {
			// Commons video falls back to a bare player without TimedMediaHandler, and
			// the module is absent on some wikis, so failing to load it is not an error.
			mw.loader.using( 'ext.tmh.player' ).catch( function () {} );
		}

		container.className = 'wikifame-custom';
		container.append.apply(
			container,
			Array.prototype.slice.call( parsed.body.childNodes )
		);
		return container;
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

	async function addHistoryIntroduction( wikiConfig ) {
		var box;
		var line;
		var custom;

		if ( document.getElementById( HISTORY_INTRO_ID ) ) {
			return;
		}

		box = document.createElement( 'div' );
		box.id = HISTORY_INTRO_ID;
		box.className = 'wikifame wikifame--history';
		box.setAttribute( 'role', 'note' );

		custom = await loadCustomContent( wikiConfig.historyIntroPage );

		if ( custom ) {
			box.append( renderCustomContent( custom ) );
		} else {
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
		}

		// Always built here, never in wikitext: a page parsed on its own has no idea
		// which article the reader is looking at, so {{FULLPAGENAME}} would name the
		// introduction itself and the link would offer to edit the wrong page.
		line = document.createElement( 'p' );
		appendMessageWithNodes( line, 'wikifame-history-edit', [ createEditLink() ] );
		box.append( line );

		insertBelowSubtitle( box );
		mw.hook( 'wikifame.history' ).fire( box, wikiConfig );

		// After the box is in the page, never before. The introduction is the reason the
		// reader is looking, and it must not wait on an API round trip to appear.
		return fillContributorCount( box );
	}

	/**
	 * Put this article's contributor count into any slot the custom content declared.
	 *
	 * A wikitext page is parsed on its own and cached for every article, so it cannot
	 * contain a per-article number. It writes an element of a known class instead, keeps
	 * plain wording inside it as a fallback, and this replaces that text once the API
	 * answers. A page that asks for no count triggers no request at all, which is what
	 * keeps history views free for everyone who does not use this.
	 */
	async function fillContributorCount( box ) {
		var phrases = box.querySelectorAll( '.wikifame-count' );
		var numbers = box.querySelectorAll( '.wikifame-number' );
		var data;
		var phrase;

		if ( !phrases.length && !numbers.length ) {
			return;
		}

		try {
			// No retry here, unlike an article view: a result that is still being computed
			// should leave the reader's own wording alone rather than rewrite the box under
			// them ten seconds after they started reading it.
			data = await contributionData( [] );
		} catch ( error ) {
			mw.log.warn( 'WikiFame: contributor count unavailable', error );
			return;
		}

		if ( !data || data.humanCount < 1 ) {
			return;
		}

		phrase = mw.message( 'wikifame-people', formatNumber( data.humanCount ) ).text();
		if ( data.limited ) {
			phrase = mw.message( 'wikifame-at-least', phrase ).text();
		}

		phrases.forEach( function ( slot ) {
			slot.textContent = phrase;
		} );
		numbers.forEach( function ( slot ) {
			slot.textContent = formatNumber( data.humanCount );
		} );
	}

	/**
	 * What the count should look like right now.
	 *
	 * 'hidden'  nothing on the page yet — the common case, because a warm response
	 *           beats the threshold and a placeholder would only invent a wait.
	 * 'rolling' digits turning while the request is genuinely slow.
	 * 'vague'   wording that holds whatever the answer turns out to be.
	 * 'final'   the real sentence.
	 *
	 * Separated from the DOM so the whole timing policy is eight readable lines.
	 */
	function countDisplayState( elapsedMs, data ) {
		if ( data ) {
			return 'final';
		}
		if ( elapsedMs < COUNT_ROLL_START_MS ) {
			return 'hidden';
		}
		if ( elapsedMs < COUNT_ROLL_SETTLE_MS ) {
			// Without animation there is nothing to gain by appearing early, and a
			// reader who asked for less motion is the last one who should be shown
			// wording that flashes past on its way to being replaced.
			return prefersReducedMotion() ? 'hidden' : 'rolling';
		}
		return 'vague';
	}

	/**
	 * How long 'hidden' lasts: until the next threshold that is still ahead.
	 *
	 * Subtracting from COUNT_ROLL_START_MS unconditionally would go negative once that
	 * threshold passes, and a zero-delay timer in a loop is a busy wait.
	 */
	function hiddenWaitMs( elapsedMs ) {
		return elapsedMs < COUNT_ROLL_START_MS ?
			COUNT_ROLL_START_MS - elapsedMs :
			COUNT_ROLL_SETTLE_MS - elapsedMs;
	}

	function prefersReducedMotion() {
		return Boolean(
			window.matchMedia &&
			window.matchMedia( '(prefers-reduced-motion: reduce)' ).matches
		);
	}

	function rollingDigits() {
		var out = '';
		var index;

		for ( index = 0; index < COUNT_ROLL_DIGITS; index++ ) {
			out += String( Math.floor( Math.random() * 10 ) );
		}
		return out;
	}

	/**
	 * The tier-three sentence with the number still missing.
	 *
	 * Hidden from assistive technology while it turns: a screen reader must not announce
	 * a half-formed sentence, let alone one digit per frame. Both attributes come off
	 * when the box settles on wording a reader can act on.
	 */
	function buildPendingSummary() {
		var box = document.createElement( 'div' );
		var digits = document.createElement( 'span' );

		box.id = ARTICLE_SUMMARY_ID;
		box.className = 'wikifame wikifame--article';
		box.setAttribute( 'role', 'note' );
		box.setAttribute( 'aria-busy', 'true' );
		box.setAttribute( 'aria-hidden', 'true' );

		digits.className = 'wikifame-rolling';
		digits.textContent = rollingDigits();

		box.append( document.createTextNode( mw.message( 'wikifame-summary-prefix' ).text() ) );
		box.append( digits );
		box.append( document.createTextNode( '.' ) );
		return box;
	}

	/**
	 * Stop turning and say something that stays true if the answer never comes.
	 *
	 * This is a refinement, not a correction: vague then precise, never wrong then right.
	 * That is what separates it from showing a number the API would later contradict.
	 */
	function settlePendingSummary( box ) {
		box.textContent = mw.message( 'wikifame-summary-prefix' ).text() +
			mw.message( 'wikifame-many-people' ).text() + '.';
		box.removeAttribute( 'aria-busy' );
		box.removeAttribute( 'aria-hidden' );
	}

	/**
	 * Drive the placeholder until the request resolves, one way or another.
	 *
	 * Exits as soon as `outcome.done` is set, so a fast answer costs one timer at most
	 * and usually none: at COUNT_ROLL_START_MS the request has normally already won.
	 */
	async function runCountPlaceholder( startedAt, outcome ) {
		var box = null;
		var digits = null;
		var display;

		while ( !outcome.done ) {
			display = countDisplayState( Date.now() - startedAt, null );

			if ( display === 'hidden' ) {
				await wait( hiddenWaitMs( Date.now() - startedAt ) );
				continue;
			}

			if ( !box ) {
				box = buildPendingSummary();
				digits = box.querySelector( '.wikifame-rolling' );
				// An unrecognised skin offers nowhere to put it. Animating a detached
				// node would burn timers nobody can see.
				if ( !insertBelowSubtitle( box ) ) {
					return;
				}
			}

			if ( display === 'rolling' ) {
				digits.textContent = rollingDigits();
				await wait( COUNT_ROLL_FRAME_MS );
				continue;
			}

			// Settled. Nothing left to animate, so stop burning timers and let the
			// awaited request replace the wording if it ever arrives.
			settlePendingSummary( box );
			return;
		}
	}

	function removeArticleSummary() {
		var existing = document.getElementById( ARTICLE_SUMMARY_ID );

		if ( existing ) {
			existing.remove();
		}
	}

	/**
	 * Render the attribution sentence, filling it in as the answer arrives.
	 *
	 * The request starts before anything is drawn, so the placeholder only ever appears
	 * when the wait is real. A page the API cannot serve — an unsupported wiki, a network
	 * failure — must leave no trace, which is why an error is told apart from a result
	 * that is merely still being computed.
	 */
	async function addArticleSummary() {
		var startedAt = Date.now();
		var outcome = { done: false, data: null, failed: false };
		var existing;
		var pending;
		var summary;

		if ( document.getElementById( ARTICLE_SUMMARY_ID ) ) {
			return;
		}

		pending = contributionData( PENDING_RETRY_DELAYS_MS ).then( function ( value ) {
			outcome.data = value;
		}, function ( error ) {
			mw.log.warn( 'WikiFame: attribution unavailable', error );
			outcome.failed = true;
		} ).then( function () {
			outcome.done = true;
		} );

		await Promise.all( [ pending, runCountPlaceholder( startedAt, outcome ) ] );

		// An error is not a slow answer: an unsupported wiki must not be left claiming
		// that many people wrote the article.
		if ( outcome.failed ) {
			removeArticleSummary();
			return;
		}

		// Still pending after every retry. The settled wording is the final answer.
		if ( !outcome.data ) {
			return;
		}

		summary = buildArticleSummary( outcome.data );
		if ( !summary ) {
			removeArticleSummary();
			return;
		}

		existing = document.getElementById( ARTICLE_SUMMARY_ID );
		if ( existing ) {
			existing.replaceWith( summary );
		} else {
			insertBelowSubtitle( summary );
		}
		mw.hook( 'wikifame.summary' ).fire( summary, outcome.data );
	}

	/**
	 * Attribution for this page, from the session cache when it is there.
	 *
	 * The cache key is the page, not the view, so a reader who looks at an article and
	 * then opens its history pays for one request rather than two. How long to wait on a
	 * result that is still being computed is the caller's decision.
	 */
	async function contributionData( retryDelaysMs ) {
		var cacheKey = getCacheKey();
		var cached = readCache( cacheKey, CLIENT_CACHE_MAX_AGE_MS );
		var data;

		if ( cached ) {
			return cached;
		}

		data = await loadContributionData( retryDelaysMs );
		if ( data ) {
			writeCache( cacheKey, data );
		}
		return data;
	}

	async function loadContributionData( retryDelaysMs ) {
		var url = TOOLFORGE_API_BASE + '/v2/' +
			encodeURIComponent( config.wgDBname ) + '/pages/' +
			encodeURIComponent( config.wgArticleId ) +
			'?revision_id=' + encodeURIComponent( config.wgCurRevisionId );
		var delays = retryDelaysMs || PENDING_RETRY_DELAYS_MS;
		var attempt;
		var data;

		for ( attempt = 0; attempt <= delays.length; attempt++ ) {
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

			if ( attempt < delays.length ) {
				await wait( delays[ attempt ] );
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
			return '\u0000' + index;
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
