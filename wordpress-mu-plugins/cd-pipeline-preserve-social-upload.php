<?php
/**
 * Plugin Name: CD Pipeline Preserve Social Upload
 * Description: Bypass WordPress big-image downscaling and Smush/ShortPixel/EWWW resize for
 *              Scoutmonkeys pipeline social uploads. Detects the X-CD-Pipeline-Social: 1
 *              request header and disables all automatic pixel-dimension reduction so the
 *              social JPEG stays at exactly 1920×1400 in the media library.
 *
 * Deploy: copy this file to wp-content/mu-plugins/ on culturaldaily.com.
 * No activation step required — mu-plugins load automatically on every request.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Is the current HTTP request a Scoutmonkeys pipeline social upload?
 * Checks for the X-CD-Pipeline-Social: 1 header sent by pipeline.py.
 */
function _cd_pipeline_is_social_upload(): bool {
	return ! empty( $_SERVER['HTTP_X_CD_PIPELINE_SOCIAL'] );
}

// --- WordPress core: disable big-image threshold ---
add_filter( 'big_image_size_threshold', function ( $threshold ) {
	if ( _cd_pipeline_is_social_upload() ) {
		return false; // false = no downscaling
	}
	return $threshold;
} );

// --- Smush (WPMU Dev): skip resize for this upload ---
add_filter( 'smush_resize_uploaded_image', function ( $resize ) {
	if ( _cd_pipeline_is_social_upload() ) {
		return false;
	}
	return $resize;
} );

// --- ShortPixel: skip processing for this attachment ---
add_filter( 'shortpixel_image_exists', function ( $exists, $attachment_id ) {
	if ( _cd_pipeline_is_social_upload() ) {
		return true; // pretend already optimised → skip
	}
	return $exists;
}, 10, 2 );

// --- EWWW Image Optimizer: skip this upload ---
add_filter( 'ewww_image_optimizer_bypass', function ( $bypass, $filename ) {
	if ( _cd_pipeline_is_social_upload() ) {
		return true;
	}
	return $bypass;
}, 10, 2 );

// --- Imagify: skip this upload ---
add_filter( 'imagify_auto_optimize_attachment', function ( $optimize ) {
	if ( _cd_pipeline_is_social_upload() ) {
		return false;
	}
	return $optimize;
} );
